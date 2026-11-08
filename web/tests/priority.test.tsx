import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { CasePriority, CaseSummary } from "../src/api/contract";
import { createMockAdapter, ROLE_NAMES } from "../src/api/mockAdapter";
import { ROLE_PROVIDERS } from "../src/api/identities";
import { caseIdFor, pathwayOfCase, priorityOfCase, seedEvents } from "../src/api/fixtures";
import { PriorityBadge } from "../src/components/PriorityBadge";
import { milestonesFor, pathwayMeta, procedurePlural, procedureSlug, MILESTONES } from "../src/lib/pathway";
import { PRIORITY_TIERS, priorityRank } from "../src/lib/priority";
import { DEFAULT_FILTERS, pathwaysOf, sortWorklist, worklist } from "../src/lib/worklist";
import { CaseDetailPage } from "../src/pages/CaseDetailPage";
import { CoordinatorPage } from "../src/pages/CoordinatorPage";
import { renderWithApp } from "./helpers";

/** Result priority (ADR-051) on the web: the third pathway (long-term conditions) and the tier that orders and explains the worklist. */

const NOW = "2026-11-04T09:00:00.000Z";
const prio = (tier: string, rank: number, basis = "PLACEHOLDER, needs clinician sign-off (H1)"): CasePriority => ({
  tier, rank, rule: `rule_${tier}`, reason: { en: `Reason ${tier}`, ar: "سبب" }, basis,
});
const row = (id: string, extra: Partial<CaseSummary> = {}): CaseSummary => ({
  case_id: id, pathway: "colorectal_fit", state: "owner_accepted", created_at: "2026-10-01T09:00:00Z", updated_at: "2026-10-01T09:00:00Z",
  owner: null, due_at: "2026-11-10T09:00:00Z", overdue: false, escalation_level: 0, consent_frozen: false, last_event_at: "2026-10-01T09:00:00Z", ...extra,
});
const threePathways = () => createMockAdapter(null, { automation: true, thirdPathway: true });
const CHRONIC = [40, 41, 42, 43].map(caseIdFor);

describe("sortWorklist with priority", () => {
  it("orders urgent, soon, routine, and treats a case with no priority like routine, ahead of an overdue or riskier case", () => {
    const cases = [
      row("a-none", { overdue: true, due_at: "2026-10-20T09:00:00Z", risk_score: 0.9 }),
      row("b-routine", { priority: prio("routine", 2) }),
      row("c-soon", { priority: prio("soon", 1) }),
      row("d-urgent", { priority: prio("urgent", 0) }),
    ];
    // the overdue no-priority case ties with routine on priority and wins the tiebreak on overdue; both follow soon and urgent
    expect(sortWorklist(cases, NOW).map((c) => c.case_id)).toEqual(["d-urgent", "c-soon", "a-none", "b-routine"]);
    // an unknown tier sorts after the known ones
    expect(sortWorklist([row("x", { priority: prio("watch", 3) }), row("y", { priority: prio("routine", 2) }), row("z")], NOW).map((c) => c.case_id)).toEqual(["y", "z", "x"]);
  });
  it("leaves a list with no priorities in exactly the previous order", () => {
    const cases = [
      row("e", { risk_score: 0.1, created_at: "2026-10-03T09:00:00Z" }),
      row("d", { overdue: true, due_at: "2026-10-30T09:00:00Z" }),
      row("c", { overdue: true, due_at: "2026-10-20T09:00:00Z" }),
      row("b", { risk_score: 0.7 }),
      row("a", { risk_score: 0.7 }),
    ];
    const previous = (list: CaseSummary[]) => [...list].sort((p, q) => {
      if (p.overdue !== q.overdue) return p.overdue ? -1 : 1;
      const od = (q.overdue ? Math.floor((Date.parse(NOW) - Date.parse(q.due_at!)) / 864e5) : 0) - (p.overdue ? Math.floor((Date.parse(NOW) - Date.parse(p.due_at!)) / 864e5) : 0);
      if (od !== 0) return od;
      const rk = (q.risk_score ?? 0) - (p.risk_score ?? 0);
      if (rk !== 0) return rk;
      const c = p.created_at.localeCompare(q.created_at);
      return c !== 0 ? c : p.case_id.localeCompare(q.case_id);
    });
    expect(sortWorklist(cases, NOW).map((c) => c.case_id)).toEqual(previous(cases).map((c) => c.case_id));
    expect(sortWorklist(cases, NOW).map((c) => c.case_id)).toEqual(["c", "d", "a", "b", "e"]);
  });
  it("ranks absent priority as routine and an unknown tier after it", () => {
    expect(PRIORITY_TIERS).toEqual(["urgent", "soon", "routine"]);
    expect(priorityRank(null)).toBe(priorityRank({ tier: "routine" }));
    expect(priorityRank(undefined)).toBe(2);
    expect(priorityRank({ tier: "urgent" })).toBeLessThan(priorityRank({ tier: "soon" }));
    expect(priorityRank({ tier: "mystery" })).toBeGreaterThan(priorityRank({ tier: "routine" }));
  });
});

describe("PriorityBadge", () => {
  it("shows the tier, the reason in the title and the placeholder note for a placeholder basis", () => {
    renderWithApp(<PriorityBadge priority={prio("urgent", 0)} />);
    const badge = screen.getByTestId("priority-badge");
    expect(badge).toHaveTextContent("Urgent");
    expect(badge).toHaveAttribute("data-priority", "urgent");
    expect(badge.getAttribute("title")).toMatch(/Reason urgent/);
    expect(badge.getAttribute("title")).toMatch(/Threshold is a placeholder pending clinician sign-off\./);
  });
  it("leaves the placeholder note out for a sourced basis, and renders nothing without a priority", () => {
    const { rerender, container } = renderWithApp(<PriorityBadge priority={prio("soon", 1, "Guideline X, table 2")} />);
    expect(screen.getByTestId("priority-badge")).toHaveTextContent("Soon");
    expect(screen.getByTestId("priority-badge").getAttribute("title")).not.toMatch(/placeholder/);
    rerender(<PriorityBadge priority={null} />);
    expect(screen.queryByTestId("priority-badge")).toBeNull();
    rerender(<PriorityBadge />);
    expect(container.querySelector("[data-priority]")).toBeNull();
  });
});

describe("the third pathway's metadata", () => {
  it("knows long-term conditions, with a 20-working-day placeholder clock and a slugged procedure id", () => {
    expect(pathwayMeta("chronic_conditions")).toMatchObject({ label: "Long-term conditions", procedure: "care plan visit", placeholderTimings: true });
    const clock = milestonesFor("chronic_conditions").find((m) => m.name === "confirmatory_completed")!;
    expect(clock.working_days).toBe(20);
    expect(clock.label).toBe("Family physician visit with a care plan within 20 working days of referral (placeholder, pending clinician sign-off)");
    expect(clock.source).toMatch(/Placeholder design value, not a standard:/);
    expect(milestonesFor("colorectal_fit")).toBe(MILESTONES);
    expect(milestonesFor("cervical_hpv").find((m) => m.name === "confirmatory_completed")!.working_days).toBe(31);
    expect(procedureSlug("care plan visit")).toBe("care-plan-visit");
    expect(procedureSlug("colonoscopy")).toBe("colonoscopy");
    expect([procedurePlural("colonoscopy"), procedurePlural("colposcopy"), procedurePlural("care plan visit")]).toEqual(["colonoscopies", "colposcopies", "care plan visits"]);
  });
});

describe("the mock seed with priorities", () => {
  it("has no chronic case and no priority unless asked", async () => {
    for (const automation of [false, true]) expect(Object.keys(seedEvents(automation)).some((id) => pathwayOfCase(id) === "chronic_conditions")).toBe(false);
    expect(Object.keys(seedEvents(true, true)).some((id) => pathwayOfCase(id) === "chronic_conditions")).toBe(false);
    expect(Object.keys(seedEvents(true, true, true)).filter((id) => pathwayOfCase(id) === "chronic_conditions")).toEqual(CHRONIC);
    const bowel = await createMockAdapter(null, { automation: true }).listCases();
    expect(bowel.every((r) => !("priority" in r))).toBe(true);
    expect(priorityOfCase(caseIdFor(1))).toBeNull();
  });
  it("gives the chronic cases and one cervical case a priority, and never shows it to a patient", async () => {
    const api = threePathways();
    const rows = await api.listCases();
    const tierOf = (n: number) => rows.find((r) => r.case_id === caseIdFor(n))?.priority?.tier;
    expect([40, 41, 42, 43].map(tierOf)).toEqual(["urgent", "soon", "routine", "routine"]);
    expect(tierOf(31)).toBe("urgent");
    expect(rows.filter((r) => r.pathway === "colorectal_fit").every((r) => !r.priority)).toBe(true);
    expect(pathwaysOf(rows)).toEqual(["colorectal_fit", "cervical_hpv", "chronic_conditions"]);
    await api.login("patient", ROLE_NAMES.patient, ROLE_PROVIDERS.patient);
    expect((await api.listCases()).every((r) => !r.priority)).toBe(true);
    expect((await api.getCase(caseIdFor(40))).priority).toBeUndefined();
  });
  it("records the confirmatory result of a chronic case under the slugged procedure label", async () => {
    const api = threePathways();
    await api.login("clinician", "Dr. Huda Rahman", ROLE_PROVIDERS.clinician); // owns case 42 (long-term conditions)
    const r = await api.recordConfirmatoryResult(caseIdFor(42));
    expect(r.evidence_ref).toBe(`Procedure/care-plan-visit-${caseIdFor(42)}`);
    expect(r.report_ref).toBe(`DiagnosticReport/care-plan-visit-report-${caseIdFor(42)}`);
  });
});

describe("worklist and case page with priorities", () => {
  it("shows a priority badge per prioritised row, urgent before soon before routine, with the Priority column", async () => {
    renderWithApp(<CoordinatorPage />, { api: threePathways() });
    const rows = await screen.findAllByTestId("worklist-row");
    expect(screen.getByRole("columnheader", { name: "Priority" })).toBeInTheDocument();
    const tiers = rows.map((r) => within(r).queryByTestId("priority-badge")?.getAttribute("data-priority") ?? "none");
    const order = ["urgent", "soon", "routine", "none"];
    const idx = tiers.map((t) => order.indexOf(t));
    expect(tiers.slice(0, 2)).toEqual(["urgent", "urgent"]);
    expect(tiers[2]).toBe("soon");
    // routine and unprioritised cases are one group (no priority counts as routine): nothing urgent or soon follows it
    expect(idx.slice(3).every((i) => i >= 2)).toBe(true);
    expect(tiers.filter((t) => t === "urgent")).toHaveLength(2);
    // the table cells carry the mobile label
    expect(within(rows[0]).getByTestId("priority-badge").closest("td")).toHaveAttribute("data-label", "Priority");
  });
  it("lists the third pathway in the pathway filter and filters to it", async () => {
    const user = userEvent.setup();
    renderWithApp(<CoordinatorPage />, { api: threePathways() });
    await screen.findAllByTestId("worklist-row");
    const select = screen.getByLabelText("Pathway");
    expect(within(select).getByRole("option", { name: "Long-term conditions" })).toBeInTheDocument();
    await user.selectOptions(select, "chronic_conditions");
    const rows = screen.getAllByTestId("worklist-row");
    expect(rows).toHaveLength(CHRONIC.length);
    expect(worklist(await threePathways().listCases(), { ...DEFAULT_FILTERS, pathway: "chronic_conditions" }, NOW)).toHaveLength(4);
  });
  it("shows the Priority row on the case page with the reason and the line that it does not change the SLA clock", async () => {
    renderWithApp(<CaseDetailPage />, { api: threePathways(), route: `/coordinator/${caseIdFor(40)}`, path: "/coordinator/:caseId" });
    const fact = await screen.findByTestId("priority");
    expect(within(fact).getByTestId("priority-badge")).toHaveAttribute("data-priority", "urgent");
    expect(within(fact).getByTestId("priority-reason")).toHaveTextContent("Very high blood pressure");
    expect(within(fact).getByTestId("priority-note")).toHaveTextContent("It does not change the SLA clock, the escalation ladder or who may close the case.");
    expect(within(fact).getByTestId("priority-note")).toHaveTextContent("The threshold is a placeholder pending clinician sign-off.");
    expect(screen.getAllByTestId("pathway-badge")[0]).toHaveAttribute("data-pathway", "chronic_conditions");
    expect(screen.getByText("Family physician visit with a care plan within 20 working days of referral (placeholder, pending clinician sign-off)")).toBeInTheDocument();
  });
  it("shows no Priority row for a case with no priority", async () => {
    renderWithApp(<CaseDetailPage />, { api: threePathways(), route: `/coordinator/${caseIdFor(9)}`, path: "/coordinator/:caseId" });
    await screen.findByTestId("pathway-badge");
    expect(screen.queryByTestId("priority")).toBeNull();
    expect(screen.queryByText("Priority")).toBeNull();
  });
});
