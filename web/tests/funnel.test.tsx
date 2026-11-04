/**
 * T-C21: the outcome funnel on the dashboard (A39, ADR-052). The head of screening described the loop in five steps; the card shows
 * them on one screen as a real table (count, share of the previous step, median time from it), with a pathway and a priority filter
 * and the cases at risk of being lost. These tests pin the wording, the table semantics, the filters, the rule that nothing is
 * claimed beyond the data, the role gate, and that the mock's answer obeys the invariants the API's is tested for.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type CaseEvent, type FunnelMetrics } from "../src/api/contract";
import { createHttpAdapter } from "../src/api/httpAdapter";
import { createMockAdapter } from "../src/api/mockAdapter";
import { DashboardPage } from "../src/pages/DashboardPage";
import { FUNNEL_STEPS, daysOf, funnelMetricsOf, median, pctOf } from "../src/lib/funnel";
import { renderWithApp } from "./helpers";

const SIM = "2026-11-04T09:00:00.000Z";
const day = (n: number) => new Date(Date.parse("2026-09-01T09:00:00.000Z") + n * 86_400_000).toISOString();
const ev = (type: string, d: number, actor = "coordinator:m", payload: Record<string, unknown> = {}): CaseEvent => ({ seq: 1, type, at: day(d), actor, payload });
const MAIN: CaseEvent[] = [ev("case_created", 0, "system:intake"), ev("acknowledge", 1, "lab:l"), ev("accept_ownership", 2, "primary_care_owner:k"), ev("contact_patient", 3, "primary_care_owner:k"), ev("patient_acknowledge", 4, "patient:p")];
const BOOKED = [...MAIN, ev("book_confirmatory", 6, "specialist_owner:s")];
const ATTENDED = [...BOOKED, ev("complete_confirmatory", 10, "specialist_owner:s")];
const CLOSED = [...ATTENDED, ev("clinician_review", 11, "specialist_owner:s"), ev("close", 14, "specialist_owner:s", { evidence_ref: "DiagnosticReport/1", signed_by: "specialist_owner:s" })];
const REFUSED = [...MAIN, ev("record_informed_refusal", 7, "primary_care_owner:k", { counselling_ref: "c1", counselled_by: "primary_care_owner:k", reason: "personal_choice" }),
  ev("close", 9, "specialist_owner:s", { outcome: "declined", evidence_ref: "c1", signed_by: "specialist_owner:s" })];
const mk = (id: string, events: CaseEvent[], pathway = "colorectal_fit", priority: string | null = null) => ({ id, events, pathway, priority });
const PATHWAYS = ["colorectal_fit", "chronic_conditions"];

describe("T-C21: the funnel's rules, as the API applies them", () => {
  const population = [mk("a", CLOSED), mk("b", ATTENDED), mk("c", BOOKED), mk("d", MAIN), mk("e", REFUSED),
    mk("f", [...MAIN, ev("handoff_requested", 5, "primary_care_owner:k", { to_provider: "x" })], "chronic_conditions", "urgent")];
  const body = funnelMetricsOf(population, SIM, {}, PATHWAYS);
  const count = (k: string) => body.steps.find((s) => s.key === k)!.count;

  it("counts a case at each step it reached, the five steps in the programme owner's order and words", () => {
    expect(body.steps.map((s) => s.key)).toEqual(["recorded", "prioritised", "referred", "attended", "documented"]);
    expect(body.steps.map((s) => s.label)).toEqual(["Result recorded", "Higher-risk people prioritised", "Referred to the appropriate service", "Attended", "Outcome documented"]);
    expect(body.steps.map((s) => s.count)).toEqual([6, 1, 4, 2, 1]);
    expect(FUNNEL_STEPS.map((s) => s.label)).toEqual(body.steps.map((s) => s.label));
  });
  it("is a funnel: documented <= attended <= referred <= recorded, and an informed refusal is counted apart, never as attended", () => {
    expect(count("documented")).toBeLessThanOrEqual(count("attended"));
    expect(count("attended")).toBeLessThanOrEqual(count("referred"));
    expect(count("referred")).toBeLessThanOrEqual(count("recorded"));
    expect(body.declined_closures).toBe(1);
  });
  it("measures each step against the one before it (ranking gates nothing) and the time between them in calendar days", () => {
    const by = Object.fromEntries(body.steps.map((s) => [s.key, s]));
    expect([by.recorded.from, by.prioritised.from, by.referred.from, by.attended.from, by.documented.from]).toEqual([null, "recorded", "recorded", "referred", "attended"]);
    expect(by.referred.conversion).toBe(round4(4 / 6));
    expect(by.attended.conversion).toBe(0.5);
    expect(by.prioritised.median_days).toBeNull(); // derived in the event that records the result: no time to report
    expect(by.referred.median_n).toBe(4);
    expect(by.referred.median_days).toBe(median([6, 6, 6, 5])); // the booking at day 6 (three cases), the handoff at day 5, from the result at day 0
    expect(by.attended.median_days).toBe(median([4, 4])); // booked at day 6, attended at day 10
    expect(by.documented.median_days).toBe(4); // attended day 10, closed day 14
  });
  it("lists the cases at risk by the closure metrics' rule: not attended, no refusal, nothing moved for 30 days or contact lost", () => {
    // At 2026-11-04: d and f last moved on day 4 (a handoff request is not pathway progress) and c on day 6, none attended; a and b
    // attended, e is a refusal. Longest-waiting first, ties by id.
    const ids = body.at_risk.cases.map((c) => c.case_id);
    expect(body.at_risk.count).toBe(3);
    expect(ids).toEqual(["d", "f", "c"]);
    expect(body.at_risk.cases.every((c) => c.idle_days >= 30 && Object.keys(c).sort().join() === "case_id,idle_days,pathway,priority,reason,state")).toBe(true);
    const lost = funnelMetricsOf([mk("g", [...MAIN, ev("mark_lost_contact", 5, "coordinator:m", { failed_attempts: 3 })])], day(6), {}, PATHWAYS);
    expect(lost.at_risk.cases[0].reason).toBe("contact_lost"); // lost contact counts the day it is marked, however recent
  });
  it("filters by pathway and by tier (`unranked` is the cases with none)", () => {
    expect(funnelMetricsOf(population, SIM, { pathway: "chronic_conditions" }, PATHWAYS).steps[0].count).toBe(1);
    expect(funnelMetricsOf(population, SIM, { priority: "urgent" }, PATHWAYS).steps[0].count).toBe(1);
    expect(funnelMetricsOf(population, SIM, { priority: "unranked" }, PATHWAYS).steps[0].count).toBe(5);
    const empty = funnelMetricsOf(population, SIM, { pathway: "chronic_conditions", priority: "soon" }, PATHWAYS);
    expect(empty.steps.map((s) => s.count)).toEqual([0, 0, 0, 0, 0]);
    expect(empty.steps.every((s) => s.conversion === null && s.median_days === null)).toBe(true);
    expect(body.options).toEqual({ pathways: PATHWAYS, priorities: ["urgent", "unranked"] });
  });
  it("formats nothing it does not have", () => {
    expect(pctOf(null)).toBe("n/a");
    expect(pctOf(0.6667)).toBe("67%");
    expect(daysOf(null)).toBe("n/a");
    expect(daysOf(0.44)).toBe("0.4 days");
    expect(daysOf(1)).toBe("1 day");
  });
});
const round4 = (x: number) => Math.round(x * 1e4) / 1e4;

describe("T-C21: the card on the dashboard", () => {
  it("is a real table: a caption, column headers, a row header per step, and the numbers as text", async () => {
    renderWithApp(<DashboardPage />);
    const table = await screen.findByTestId("funnel-table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Step", "Cases", "Of the previous step", "Median time from the previous step"]);
    const rows = within(table).getAllByRole("rowheader");
    expect(rows.map((r) => within(r).getByText(/^(Result recorded|Higher-risk people prioritised|Referred to the appropriate service|Attended|Outcome documented)$/).textContent)).toEqual([
      "Result recorded", "Higher-risk people prioritised", "Referred to the appropriate service", "Attended", "Outcome documented"]);
    expect(table.querySelector("caption")).toHaveTextContent(/screening loop/i);
    // the bars are decoration for sighted users; the numbers carry the meaning
    expect(table.querySelectorAll(".funnel-bar[aria-hidden='true']")).toHaveLength(5);
    expect(within(table).queryByRole("img")).toBeNull();
    const api = createMockAdapter(null);
    const m = (await api.funnelMetrics())!;
    for (const s of m.steps) expect(screen.getByTestId(`funnel-count-${s.key}`)).toHaveTextContent(String(s.count));
  });

  it("says synthetic data and claims no outcome beyond the counts", async () => {
    renderWithApp(<DashboardPage />);
    const card = await screen.findByTestId("funnel-card");
    expect(within(card).getByTestId("funnel-synthetic")).toHaveTextContent("Synthetic data");
    expect(card).toHaveTextContent(/simulated clock/i);
    expect(screen.getByTestId("funnel-note")).toHaveTextContent(/not people helped/);
    expect(screen.getByTestId("funnel-note")).toHaveTextContent(/not a measure of health outcomes/);
    expect(card.textContent).not.toMatch(/\b(saved lives|improved outcomes|40% (target )?(is )?met)\b/i);
  });

  it("shows the cases at risk with a link to each case and to the worklist, and the definition it counts by", async () => {
    const m = funnelMetricsOf([mk("d", MAIN), mk("c", BOOKED), mk("a", CLOSED)], SIM, {}, PATHWAYS);
    renderWithApp(<DashboardPage />, { api: { ...createMockAdapter(null), funnelMetrics: async () => m } });
    const risk = await screen.findByTestId("funnel-at-risk");
    expect(within(risk).getByTestId("funnel-risk-count")).toHaveTextContent(String(m.at_risk.count));
    expect(risk).toHaveTextContent(/30 days or more on the simulated clock/);
    const rows = within(risk).getAllByTestId("funnel-risk-row");
    expect(rows).toHaveLength(m.at_risk.cases.length);
    expect(within(rows[0]).getByRole("link")).toHaveAttribute("href", `/coordinator/${m.at_risk.cases[0].case_id}`);
    expect(within(risk).getByTestId("funnel-worklist-link")).toHaveAttribute("href", "/coordinator?at_risk=1");
  });

  it("re-asks the API when a filter changes, with exactly that filter, and offers the pathways and tiers the server runs", async () => {
    const user = userEvent.setup();
    const base = createMockAdapter(null, { thirdPathway: true });
    const spy = vi.spyOn(base, "funnelMetrics");
    renderWithApp(<DashboardPage />, { api: base });
    await screen.findByTestId("funnel-table");
    expect(spy).toHaveBeenLastCalledWith({ pathway: undefined, priority: undefined });
    const pathway = screen.getByTestId("funnel-pathway") as HTMLSelectElement;
    const priority = screen.getByTestId("funnel-priority") as HTMLSelectElement;
    expect([...pathway.options].map((o) => o.textContent)).toEqual(["All pathways", "Bowel screening", "Cervical screening", "Long-term conditions"]);
    expect([...priority.options].map((o) => o.textContent)).toEqual(["All priorities", "Urgent", "Soon", "Routine", "Not ranked"]);
    const all = Number(screen.getByTestId("funnel-count-recorded").textContent);
    await user.selectOptions(pathway, "chronic_conditions");
    await waitFor(() => expect(spy).toHaveBeenLastCalledWith({ pathway: "chronic_conditions", priority: undefined }));
    await waitFor(() => expect(Number(screen.getByTestId("funnel-count-recorded").textContent)).toBeLessThan(all));
    await user.selectOptions(priority, "urgent");
    await waitFor(() => expect(spy).toHaveBeenLastCalledWith({ pathway: "chronic_conditions", priority: "urgent" }));
    expect(screen.getByLabelText("Pathway")).toBe(pathway); // real labels, not placeholders
    expect(screen.getByLabelText("Priority")).toBe(priority);
  });

  it("is left out for a role that may not read it, and when the server has no such route", async () => {
    renderWithApp(<DashboardPage />, { role: "clinician" });
    await screen.findByTestId("kpi-p50");
    expect(screen.queryByTestId("funnel-card")).toBeNull();
    const api = { ...createMockAdapter(null), funnelMetrics: async () => null };
    renderWithApp(<DashboardPage />, { api });
    await screen.findAllByTestId("kpi-p50");
    await waitFor(() => expect(screen.queryByTestId("funnel-card")).toBeNull());
  });

  it("says so when the funnel cannot be loaded, and still shows the rest of the dashboard", async () => {
    const api = { ...createMockAdapter(null), funnelMetrics: async () => { throw new ApiError(500, "boom"); } };
    renderWithApp(<DashboardPage />, { api });
    expect(await screen.findByTestId("funnel-error")).toHaveTextContent("Could not load the funnel: boom");
    expect(await screen.findByTestId("kpi-p50")).toBeInTheDocument();
  });

  it("names the cases it could not count, and a closed-on-refusal count, when there are any", async () => {
    const wire: FunnelMetrics = {
      sim_time: SIM, filters: { pathway: null, priority: null }, options: { pathways: ["colorectal_fit"], priorities: ["urgent", "unranked"] },
      steps: FUNNEL_STEPS.map((s, i) => ({ key: s.key, label: s.label, definition: s.definition, count: [9, 0, 4, 2, 1][i], share_of_recorded: 0.5, from: s.from, conversion: s.from ? 0.5 : null, median_days: s.key === "recorded" ? null : 2.5, median_n: 2 })),
      declined_closures: 3, at_risk: { count: 14, idle_days_threshold: 30, cases: [], truncated: true, limit: 10 }, replay_failures: { count: 2 },
    };
    const api = { ...createMockAdapter(null), funnelMetrics: async () => wire };
    renderWithApp(<DashboardPage />, { api });
    expect(await screen.findByTestId("funnel-declined")).toHaveTextContent("Closed on an informed refusal: 3");
    expect(screen.getByTestId("funnel-failures")).toHaveTextContent("2 cases could not be counted");
    expect(screen.getByTestId("funnel-median-referred")).toHaveTextContent("2.5 days");
  });
});

describe("T-C21: the mock answers like the API", () => {
  it("is staff-only and filters are validated", async () => {
    const api = createMockAdapter(null);
    await api.login("patient", "p");
    expect(await api.funnelMetrics()).toBeNull();
    await api.login("coordinator", "c");
    await expect(api.funnelMetrics({ pathway: "no_such_pack" })).rejects.toMatchObject({ status: 422 });
  });
  it("satisfies the funnel invariants on the seeded cases of all three pathways", async () => {
    const api = createMockAdapter(null, { thirdPathway: true });
    const c = Object.fromEntries((await api.funnelMetrics())!.steps.map((s) => [s.key, s.count]));
    expect(c.documented).toBeLessThanOrEqual(c.attended);
    expect(c.attended).toBeLessThanOrEqual(c.referred);
    expect(c.referred).toBeLessThanOrEqual(c.recorded);
    expect(c.prioritised).toBeGreaterThan(0); // the long-term-conditions and cervical demo cases carry a tier
    const parts = await Promise.all(["colorectal_fit", "cervical_hpv", "chronic_conditions"].map((p) => api.funnelMetrics({ pathway: p })));
    expect(parts.reduce((n, m) => n + m!.steps[0].count, 0)).toBe(c.recorded);
  });
});

describe("T-C21: the http adapter", () => {
  const make = (status: number, body: unknown) => {
    const calls: string[] = [];
    const fn = (async (url: string | URL | Request) => { calls.push(String(url)); return new Response(JSON.stringify(body), { status }); }) as typeof fetch;
    return { api: createHttpAdapter("http://api.test/", fn), calls };
  };
  it("asks GET /metrics/funnel with only the filters that are set", async () => {
    const { api, calls } = make(200, { steps: [] });
    await api.funnelMetrics();
    await api.funnelMetrics({ pathway: "cervical_hpv" });
    await api.funnelMetrics({ pathway: "chronic_conditions", priority: "unranked" });
    expect(calls).toEqual(["http://api.test/metrics/funnel", "http://api.test/metrics/funnel?pathway=cervical_hpv", "http://api.test/metrics/funnel?pathway=chronic_conditions&priority=unranked"]);
  });
  it("degrades to null on 404 and 403, and lets a 422 or a server error through", async () => {
    expect(await make(404, { detail: "Not Found" }).api.funnelMetrics()).toBeNull();
    expect(await make(403, { detail: "role may not call this route" }).api.funnelMetrics()).toBeNull();
    await expect(make(422, { detail: "pathway must be one of" }).api.funnelMetrics({ pathway: "x" })).rejects.toMatchObject({ status: 422 });
    await expect(make(500, { detail: "boom" }).api.funnelMetrics()).rejects.toMatchObject({ status: 500 });
  });
});
