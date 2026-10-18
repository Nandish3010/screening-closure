import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Route, Routes } from "react-router-dom";
import { AGENCY_DEPARTMENT_LABEL, AGENCY_DEPARTMENTS, ApiError, type AgencyRun, type ApiClient } from "../src/api/contract";
import { createMockAdapter } from "../src/api/mockAdapter";
import { AGENCY_PRINCIPLE_FALLBACK, AgencyPage } from "../src/pages/AgencyPage";
import { CaseDetailPage } from "../src/pages/CaseDetailPage";
import { Header } from "../src/components/Header";
import { renderWithApp } from "./helpers";

const cid = (n: number) => `c${String(n).padStart(7, "0")}-0000-4000-8000-${String(n).padStart(12, "0")}`;

describe("A22: agency page", () => {
  it("opens with the principle: agents prepare, people decide", async () => {
    renderWithApp(<AgencyPage />);
    const p = await screen.findByTestId("agency-principle");
    expect(p).toHaveTextContent(/agents prepare work; people decide/i);
    expect(p).toHaveTextContent(/no agent can close a case/i);
  });

  it("falls back to the built-in principle when the API sends none", async () => {
    const base = createMockAdapter(null);
    const api: ApiClient = { ...base, agencyOrg: async () => ({ ...(await base.agencyOrg()), principle: "" }) };
    renderWithApp(<AgencyPage />, { api });
    await screen.findByTestId("agency-org");
    expect(screen.getByTestId("agency-principle")).toHaveTextContent(AGENCY_PRINCIPLE_FALLBACK);
  });

  it("draws the org chart: a supervisor, five labelled departments and their agents with counts, tools and what they replace", async () => {
    const { api } = renderWithApp(<AgencyPage />);
    const overdueOpen = (await api.listCases({ overdue: true })).filter((c) => c.state !== "closed").length;
    const org = await screen.findByTestId("agency-org");
    expect(within(org).getByRole("heading", { name: "Supervisor" })).toBeInTheDocument();
    for (const d of AGENCY_DEPARTMENTS) {
      const region = within(org).getByRole("region", { name: `${AGENCY_DEPARTMENT_LABEL[d]} department` });
      expect(within(region).getByRole("heading", { level: 3, name: AGENCY_DEPARTMENT_LABEL[d] })).toBeInTheDocument();
    }
    expect(within(org).getAllByRole("region", { name: /department$/ })).toHaveLength(5);

    const quality = screen.getByTestId("dept-quality_ops");
    expect(within(quality).getAllByRole("listitem").length).toBeGreaterThan(4);
    const sla = within(quality).getByTestId("agent-sla_risk_explainer");
    expect(within(sla).getByTestId("count-done")).toHaveTextContent(new RegExp(`done\\s*${overdueOpen}`)); // one per overdue open case
    expect(within(sla).getByTestId("count-needs_human")).toHaveTextContent(/needs human\s*0/);
    expect(within(sla).getByText("read_timeline")).toBeInTheDocument();
    expect(sla).toHaveTextContent(/Replaces:/);

    const closure = within(screen.getByTestId("dept-evidence_closure")).getByTestId("agent-prepare_closure_pack");
    expect(within(closure).getByTestId("count-needs_human")).toHaveTextContent(/needs human\s*1/);
    expect(within(screen.getByTestId("dept-referral_scheduling")).getByTestId("agent-booking_followup")).toHaveTextContent(/failed\s*1/);
  });

  it("lists what needs a person, with prepared material and a link to the case, and offers no button that acts", async () => {
    renderWithApp(<AgencyPage />);
    const list = await screen.findByTestId("agency-inbox");
    const items = within(list).getAllByTestId("inbox-item");
    expect(items.length).toBeGreaterThanOrEqual(5);
    expect(screen.getByRole("heading", { level: 2, name: "Needs a person" })).toBeInTheDocument();

    const closure = items.find((i) => i.textContent?.includes("prepare_closure_pack"))!;
    expect(closure).toHaveTextContent(/Closure pack ready for sign-off/);
    expect(closure).toHaveTextContent(/Evidence & Closure/);
    expect(closure).toHaveTextContent(/primary care owner/i);
    expect(closure).toHaveTextContent(/2026-11-0\d \d\d:\d\d UTC/);
    expect(closure).toHaveTextContent(/Recommended:/);
    expect(closure).toHaveTextContent(/Evidence ref\s*DiagnosticReport\/colonoscopy-0010/);
    expect(closure).toHaveTextContent(/Checklist\s*Evidence attached, Clinician review signed, Patient informed/); // arrays are joined
    expect(within(closure).getByRole("link", { name: /open case/i })).toHaveAttribute("href", `/coordinator/${cid(10)}`);

    const draft = items.find((i) => i.textContent?.includes("draft_message"))!;
    expect(draft).toHaveTextContent(/d-seed-2/);

    // Every item links to its case and nothing in the inbox is a button.
    expect(within(list).getAllByRole("link", { name: /open case/i })).toHaveLength(items.length);
    expect(within(list).queryAllByRole("button")).toHaveLength(0);
    expect(within(screen.getByTestId("agency-inbox-panel")).queryAllByRole("button")).toHaveLength(0);
  });

  it("shows the latest run as a timeline grouped by department, with the supervisor's reason for each dispatch and the planner's provenance", async () => {
    const user = userEvent.setup();
    renderWithApp(<AgencyPage />);
    const detail = await screen.findByTestId("run-detail");
    expect(screen.getByTestId("run-select")).toHaveValue("run-0001");
    expect(screen.getByTestId("run-status")).toHaveTextContent("Done");
    expect(within(detail).getByTestId("planner-provenance")).toHaveTextContent(/Provider\s*template/);
    expect(within(detail).getByTestId("planner-provenance")).toHaveTextContent(/Fallback used\s*yes/);

    const outreach = await screen.findByTestId("run-group-outreach");
    expect(within(outreach).getAllByTestId("run-task")[0]).toHaveAttribute("data-agent-type", "department_head");
    expect(within(outreach).getByTestId("dispatch-rationale")).toHaveTextContent(/from the current state of the cases/);
    expect(within(outreach).getAllByText("draft_message").length).toBeGreaterThan(0);
    expect(within(outreach).getAllByRole("link", { name: /^Case c0000/ }).length).toBeGreaterThan(0);
    for (const g of AGENCY_DEPARTMENTS) expect(screen.getByTestId(`run-group-${g}`)).toBeInTheDocument();
    expect(screen.getByTestId("run-group-supervisor")).toBeInTheDocument();
    expect(screen.queryByTestId("run-budget")).toBeNull();

    // Only the runs that exist can be picked.
    expect(within(screen.getByTestId("run-select")).getAllByRole("option")).toHaveLength(1);
    await user.selectOptions(screen.getByTestId("run-select"), "run-0001");
  });

  it("highlights a run that stopped because its budget was exhausted", async () => {
    const base = createMockAdapter(null);
    const api: ApiClient = {
      ...base,
      agencyRuns: async (limit) => (await base.agencyRuns(limit)).map((r): AgencyRun => ({ ...r, status: "budget_exhausted" })),
    };
    renderWithApp(<AgencyPage />, { api });
    expect(await screen.findByTestId("run-budget")).toHaveTextContent(/budget exhausted/i);
    expect(screen.getByTestId("run-status")).toHaveTextContent("Budget exhausted");
  });

  it("the supervisor can run the agency: a first run is created, a second at the same sim time says it already ran", async () => {
    const user = userEvent.setup();
    renderWithApp(<AgencyPage />, { role: "supervisor" });
    await screen.findByTestId("run-detail");
    await user.click(screen.getByTestId("run-agency"));
    await waitFor(() => expect(screen.getByTestId("run-result")).toHaveTextContent(/run-0002 started/i));
    await waitFor(() => expect(screen.getByTestId("run-select")).toHaveValue("run-0002"));
    expect(within(screen.getByTestId("run-select")).getAllByRole("option")).toHaveLength(2);
    await user.click(screen.getByTestId("run-agency"));
    await waitFor(() => expect(screen.getByTestId("run-result")).toHaveTextContent(/already ran at this time/i));
    expect(within(screen.getByTestId("run-select")).getAllByRole("option")).toHaveLength(2);
  });

  it("shows the run button to the supervisor only", async () => {
    renderWithApp(<AgencyPage />, { role: "coordinator" });
    await screen.findByTestId("run-detail");
    expect(screen.queryByTestId("run-agency")).toBeNull();
  });

  it("tells a patient it is not available and loads nothing", async () => {
    const api = createMockAdapter(null);
    let calls = 0;
    for (const m of ["agencyOrg", "agencyRuns", "agencyTasks", "agencyInbox"] as const) {
      const orig = api[m].bind(api) as (...a: unknown[]) => Promise<unknown>;
      (api as unknown as Record<string, unknown>)[m] = (...a: unknown[]) => { calls += 1; return orig(...a); };
    }
    renderWithApp(<AgencyPage />, { api, role: "patient" });
    expect(await screen.findByTestId("agency-denied")).toHaveTextContent(/not available for this role/i);
    expect(screen.queryByTestId("agency-org")).toBeNull();
    expect(calls).toBe(0);
  });

  it("an owner role sees only the tasks on cases they own, and no org chart", async () => {
    const api = createMockAdapter(null);
    await api.login("clinician", "Dr. Huda Rahman");
    renderWithApp(<AgencyPage />, { api, role: "clinician" });
    const items = await screen.findAllByTestId("inbox-item");
    expect(items).toHaveLength(1); // Dr. Huda owns case 10 only
    expect(items[0]).toHaveTextContent(/Closure pack ready/);
    expect(screen.queryByTestId("agency-org")).toBeNull();
    expect(screen.queryByTestId("agency-runs")).toBeNull();
    expect(screen.getByTestId("agency-inbox-only")).toBeInTheDocument();
  });

  it("says plainly when the backend has no agency", async () => {
    const base = createMockAdapter(null);
    const api: ApiClient = { ...base, agencyOrg: async () => { throw new ApiError(404, "Not Found"); }, agencyRuns: async () => [], agencyInbox: async () => [] };
    renderWithApp(<AgencyPage />, { api });
    expect(await screen.findByTestId("agency-org-problem")).toHaveTextContent(/not available/i);
    expect(await screen.findByTestId("inbox-empty")).toHaveTextContent(/nothing needs a person/i);
    expect(screen.getByTestId("runs-empty")).toBeInTheDocument();
  });

  it("drops an item from the inbox once a person has acted on the case", async () => {
    const api = createMockAdapter(null);
    // The owner signs case 10 off on the case page: the closure pack the agent prepared is no longer waiting for anyone.
    await api.login("clinician", "Dr. Huda Rahman");
    await api.postEvent(cid(10), { type: "close", actor: "primary_care_owner:Dr. Huda Rahman", payload: { evidence_ref: "DiagnosticReport/colonoscopy-0010", signed_by: "Dr. Huda Rahman" } });
    await api.login("coordinator", "Maryam Al Suwaidi");
    renderWithApp(<AgencyPage />, { api });
    const items = await screen.findAllByTestId("inbox-item");
    expect(items.some((i) => i.textContent?.includes("prepare_closure_pack"))).toBe(false);
  });
});

describe("A22: navigation", () => {
  const nav = (role: "coordinator" | "supervisor" | "owner" | "patient") => renderWithApp(<Header />, { role });
  it("offers the Agency link to the coordinator and the supervisor only", async () => {
    for (const role of ["coordinator", "supervisor"] as const) {
      const { unmount } = nav(role);
      expect(screen.getByRole("link", { name: "Agency" })).toHaveAttribute("href", "/agency");
      unmount();
    }
    for (const role of ["owner", "patient"] as const) {
      const { unmount } = nav(role);
      expect(screen.queryByRole("link", { name: "Agency" })).toBeNull();
      unmount();
    }
  });
});

describe("A22: the chip on the case page", () => {
  const page = (n: number, role: "coordinator" | "owner" | "clinician" | "supervisor" | "patient" = "coordinator", api?: ApiClient) =>
    renderWithApp(
      <Routes><Route path="/coordinator/:caseId" element={<CaseDetailPage />} /></Routes>,
      { role, api, route: `/coordinator/${cid(n)}` },
    );

  it("reads 'agency: N tasks, M needing you' and toggles the list of the case's tasks", async () => {
    const user = userEvent.setup();
    page(15);
    const chip = await screen.findByTestId("agency-chip");
    expect(chip).toHaveTextContent("agency: 2 tasks, 1 needing you");
    expect(chip).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("agency-chip-list")).toBeNull();
    await user.click(chip);
    expect(chip).toHaveAttribute("aria-expanded", "true");
    const rows = within(screen.getByTestId("agency-chip-list")).getAllByTestId("agency-chip-task");
    expect(rows).toHaveLength(2);
    const row = rows.find((r) => r.textContent?.includes("Referral needs a new provider"))!;
    expect(row).toHaveTextContent(/Needs human/);
    expect(row).toHaveTextContent(/did not answer/);
    expect(rows.find((r) => r.textContent?.includes("SLA risk explained"))).toHaveTextContent(/Done/);
    await user.click(chip);
    expect(screen.queryByTestId("agency-chip-list")).toBeNull();
  });

  it("counts several tasks and says '0 needing you' when they are all done", async () => {
    page(1);
    expect(await screen.findByTestId("agency-chip")).toHaveTextContent("agency: 2 tasks, 0 needing you");
  });

  it("shows nothing for a case the agency has no tasks on", async () => {
    page(11);
    await screen.findByRole("heading", { level: 1, name: /Case/ });
    await waitFor(() => expect(screen.queryByTestId("agency-chip")).toBeNull());
  });

  it("shows nothing when the call fails or returns null", async () => {
    for (const caseAgency of [async () => null, async () => { throw new ApiError(500, "boom"); }]) {
      const base = createMockAdapter(null);
      const { unmount } = page(15, "coordinator", { ...base, caseAgency });
      await screen.findByRole("heading", { level: 1, name: /Case/ });
      await new Promise((r) => setTimeout(r, 20));
      expect(screen.queryByTestId("agency-chip")).toBeNull();
      unmount();
    }
  });

  it("shows nothing when the client has no agency at all (older adapter)", async () => {
    const base = createMockAdapter(null);
    const { caseAgency: _drop, ...older } = base;
    page(15, "coordinator", older as unknown as ApiClient);
    await screen.findByRole("heading", { level: 1, name: /Case/ });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("agency-chip")).toBeNull();
  });

  it("is a staff view: an owner sees it on their own case only", async () => {
    const own = createMockAdapter(null);
    await own.login("owner", "Dr. Layla Haddad", "Riverside Primary Care");
    page(15, "owner", own);
    expect(await screen.findByTestId("agency-chip")).toBeInTheDocument();
  });
});
