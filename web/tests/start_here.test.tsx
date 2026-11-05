/** ADR-057: Start here, the fresh-case guide, the status line and reset control, the missed follow-up, the opt-in credentials. */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { ApiClient, Role } from "../src/api/contract";
import { ApiError } from "../src/api/contract";
import { createMockAdapter } from "../src/api/mockAdapter";
import { AppProvider } from "../src/app/AppContext";
import App from "../src/App";
import { CaseDetailPage } from "../src/pages/CaseDetailPage";
import { Route, Routes } from "react-router-dom";

const demoApi = (patch: Partial<ApiClient> = {}) => createMockAdapter(null, { automation: true, thirdPathway: true, demo: true, ...patch as object }) as ApiClient;

function renderApp(api: ApiClient, route = "/", role: Role = "coordinator") {
  return render(<MemoryRouter initialEntries={[route]}><AppProvider api={api} initialRole={role}><App /></AppProvider></MemoryRouter>);
}

beforeEach(() => { try { sessionStorage.clear(); } catch { /* none */ } });

describe("Start here", () => {
  it("is the first screen after sign-in in the demo: one card per step, a claim and one button each, and a way to skip", async () => {
    renderApp(demoApi(), "/");
    const page = await screen.findByTestId("start-here");
    const cards = within(page).getByTestId("start-steps").querySelectorAll("li");
    expect(cards).toHaveLength(7);
    for (const id of ["record", "prioritise", "refer", "remind", "outcome", "safety", "privacy"]) {
      const card = screen.getByTestId(`start-step-${id}`);
      expect(within(card).getAllByRole("button")).toHaveLength(1);
      expect(card.querySelector(".start-claim")?.textContent?.length).toBeGreaterThan(20);
    }
    expect(screen.getByTestId("start-skip")).toHaveAttribute("href", "/coordinator/today");
    expect(page.textContent).toMatch(/about \d+ minutes/);
  });

  it("is not the home page outside the demo", async () => {
    renderApp(createMockAdapter(null, { automation: true }), "/");
    await waitFor(() => expect(screen.queryByTestId("start-here")).toBeNull());
    expect(screen.queryByRole("link", { name: "Start here" })).toBeNull();
  });

  it("opens the exact screen on an untouched case, and the panel says what to look for", async () => {
    renderApp(demoApi(), "/start");
    fireEvent.click(await screen.findByTestId("start-open-prioritise"));
    expect(await screen.findByTestId("judge-tour")).toBeInTheDocument();
    expect(screen.getByTestId("tour-claim").textContent).toMatch(/priority tier/i);
    expect(screen.getByTestId("tour-look").textContent).toMatch(/Priority line/);
    expect(screen.getByTestId("tour-progress")).toHaveTextContent("Step 2 of 7");
    expect(await screen.findByTestId("priority")).toBeInTheDocument(); // the case page, on an urgent case
  });

  it("gives two judge sessions different cases, and never one that has been touched", async () => {
    const api = demoApi();
    const a = await api.demoGuide!("session-aaaaaaaa");
    const b = await api.demoGuide!("session-bbbbbbbb");
    const idsA = Object.entries(a.steps).filter(([k, v]) => v && !["prioritise", "privacy"].includes(k)).map(([, v]) => v!.case_id);
    const idsB = Object.entries(b.steps).filter(([k, v]) => v && !["prioritise", "privacy"].includes(k)).map(([, v]) => v!.case_id);
    expect(idsA.length).toBeGreaterThan(2);
    expect(idsA.filter((x) => idsB.includes(x))).toEqual([]);
    expect(new Set(idsA).size).toBe(idsA.length); // and one session never gets the same case for two steps
    // act on A's "record" case: it is no longer offered as untouched
    await api.login("coordinator", "Maryam Al Suwaidi");
    const touched = a.steps.record!.case_id;
    const detail = await api.getCase(touched);
    if (detail.state === "result_received") await api.postEvent(touched, { type: "acknowledge", actor: "coordinator:Maryam Al Suwaidi", payload: {} });
    else await api.postEvent(touched, { type: "contact_patient", actor: "coordinator:Maryam Al Suwaidi", payload: { channel: "phone" } }).catch(() => undefined);
    const again = await api.demoGuide!("session-aaaaaaaa");
    expect(again.steps.record?.case_id).not.toBe(touched);
  });

  it("says plainly when the guide cannot pick cases, and still works", async () => {
    const api = demoApi();
    api.demoGuide = async () => { throw new ApiError(500, "boom"); };
    renderApp(api, "/start");
    expect(await screen.findByTestId("start-no-guide")).toHaveTextContent(/first fitting case/);
    expect(screen.queryByText(/boom/)).toBeNull();
  });
});

describe("demo status and reset", () => {
  it("shows when the demo was last reset, by whom, the simulated time and the next scheduled reset", async () => {
    const api = demoApi();
    api.health = async () => ({ status: "ok", sim_time: "2026-11-04T09:00:00Z", demo: {
      simulated_clock: true, deployment: true, last_reset_at: "2026-11-08T06:00:00+00:00", last_reset_by: "the schedule", last_reset_reason: "scheduled", next_auto_reset_at: "2026-11-08T12:00:00+00:00", reset_pending: false } });
    renderApp(api, "/start");
    const line = (await screen.findAllByTestId("demo-status"))[0];
    expect(line.textContent).toContain("Demo status:");
    expect(line.textContent).toContain("Last reset 2026-11-08 06:00 UTC by the schedule");
    expect(line.textContent).toContain("simulated time 2026-11-04 09:00 UTC");
    expect(line.textContent).toContain("next scheduled reset 2026-11-08 12:00 UTC");
  });

  it("lets only a supervisor reset, asks first, and reports a refusal in plain words", async () => {
    const api = demoApi();
    const request = vi.fn(async () => { throw new ApiError(429, "The demo was reset a moment ago. Please wait 7 minute(s) before resetting again."); });
    api.requestDemoReset = request;
    const { unmount } = renderApp(api, "/start", "coordinator");
    const open = await screen.findByTestId("reset-demo-open");
    expect(open).toBeDisabled();
    expect(screen.getByTestId("reset-demo").textContent).toMatch(/Supervisors only/);
    unmount();
    renderApp(api, "/start", "supervisor");
    fireEvent.click((await screen.findAllByTestId("reset-demo-open"))[0]);
    expect(screen.getByTestId("reset-demo-confirm").textContent).toMatch(/loses their changes/);
    expect(request).not.toHaveBeenCalled(); // asking first
    fireEvent.click(screen.getByTestId("reset-demo-yes"));
    expect(await screen.findByTestId("reset-demo-message")).toHaveTextContent("Please wait 7 minute(s)");
    expect(request).toHaveBeenCalledTimes(1);
  });
});

describe("Simulate a missed follow-up", () => {
  function renderCase(api: ApiClient, id: string, role: Role) {
    return render(<MemoryRouter initialEntries={[`/coordinator/${id}`]}><AppProvider api={api} initialRole={role}>
      <Routes><Route path="/coordinator/:caseId" element={<CaseDetailPage />} /></Routes></AppProvider></MemoryRouter>);
  }

  it("is for the supervisor, explains that the clock is shared, and the engine's own events appear on the case", async () => {
    const api = demoApi();
    await api.login("supervisor", "Dr. Noura Al Hammadi");
    const g = await api.demoGuide!("session-aaaaaaaa");
    const id = g.steps.safety!.case_id;
    renderCase(api, id, "supervisor");
    const panel = await screen.findByTestId("missed-followup");
    expect(panel.textContent).toMatch(/shared/);
    fireEvent.click(screen.getByTestId("simulate-missed"));
    const result = await screen.findByTestId("missed-result");
    expect(result.textContent).toMatch(/not a person/);
    expect(result.textContent).toMatch(/pending escalation/);
    await waitFor(() => expect(screen.getByTestId("escalation-level").textContent).toMatch(/Level 2/));
    expect(screen.getByTestId("escalation-level").textContent).toMatch(/waiting for a human to accept/);
  });

  it("explains instead of looking dead when the role is not the supervisor", async () => {
    const api = demoApi();
    const g = await api.demoGuide!("session-aaaaaaaa");
    renderCase(api, g.steps.safety!.case_id, "coordinator");
    expect(await screen.findByTestId("simulate-missed")).toBeDisabled();
    expect(screen.getByTestId("simulate-missed-why").textContent).toMatch(/Supervisors only/);
  });

  it("does not exist outside the demo", async () => {
    const api = createMockAdapter(null, { automation: true });
    renderCase(api, (await api.listCases())[0].case_id, "supervisor");
    await screen.findByTestId("owner");
    expect(screen.queryByTestId("missed-followup")).toBeNull();
    expect(api.missedFollowup).toBeUndefined();
  });

  it("shows the server's plain refusal, not a stack trace", async () => {
    const api = demoApi();
    const g = await api.demoGuide!("session-aaaaaaaa");
    api.missedFollowup = async () => { throw new ApiError(409, "This case's follow-up is 80 days away; that is too far to skip in one go. Pick a case that is due sooner."); };
    renderCase(api, g.steps.safety!.case_id, "supervisor");
    fireEvent.click(await screen.findByTestId("simulate-missed"));
    expect(await screen.findByTestId("missed-error")).toHaveTextContent("too far to skip");
  });
});
