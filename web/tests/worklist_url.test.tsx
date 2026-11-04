/** T-C22 (C14): the worklist's filters live in the URL, so `/coordinator?at_risk=1` deep-links; at_risk is the metrics' rule. */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ApiClient, CaseEvent } from "../src/api/contract";
import { createMockAdapter } from "../src/api/mockAdapter";
import { atRiskOf } from "../src/lib/derive";
import { DEFAULT_FILTERS, filterWorklist, filtersFromParams, paramsFromFilters } from "../src/lib/worklist";
import { CoordinatorPage } from "../src/pages/CoordinatorPage";
import { renderWithApp } from "./helpers";

const ev = (type: string, at: string, actor = "coordinator:x"): CaseEvent => ({ seq: 1, type, at, actor, payload: {} });
const created = ev("case_created", "2026-10-01T00:00:00Z", "system:intake");

describe("at_risk is lost_to_follow_up", () => {
  it("lost-contact flag, or 30 idle days without main-path progress; never after a confirmatory or a refusal", () => {
    expect(atRiskOf([created], "2026-10-20T00:00:00Z")).toBe(false);
    expect(atRiskOf([created], "2026-10-31T00:00:00Z")).toBe(true); // 30 days exactly
    expect(atRiskOf([created, ev("acknowledge", "2026-10-25T00:00:00Z")], "2026-11-10T00:00:00Z")).toBe(false); // progress resets
    expect(atRiskOf([created, ev("mark_lost_contact", "2026-10-02T00:00:00Z")], "2026-10-03T00:00:00Z")).toBe(true);
    expect(atRiskOf([created, ev("complete_confirmatory", "2026-10-02T00:00:00Z")], "2026-12-30T00:00:00Z")).toBe(false);
    expect(atRiskOf([created, ev("annotation_only", "2026-10-30T00:00:00Z")], "2026-11-05T00:00:00Z")).toBe(true); // not progress
  });
});

/** The mock's seed has no lost case, so mark two open cases at risk on the way out. */
const withRisk = (base: ApiClient): ApiClient =>
  Object.assign(Object.create(base), base, {
    listCases: async () => (await base.listCases()).map((c, i) => (c.state !== "closed" && i % 4 === 0 ? { ...c, at_risk: true } : c)),
  }) as ApiClient;

describe("filters in the URL", () => {
  it("round-trips and leaves defaults out", () => {
    const f = { ...DEFAULT_FILTERS, pathway: "cervical_hpv", priority: "urgent", atRisk: true, overdueOnly: true };
    expect(paramsFromFilters(f).toString()).toBe("pathway=cervical_hpv&priority=urgent&overdue=1&at_risk=1");
    expect(filtersFromParams(new URLSearchParams(paramsFromFilters(f)))).toEqual(f);
    expect(paramsFromFilters(DEFAULT_FILTERS).toString()).toBe("");
    expect(filtersFromParams(new URLSearchParams("at_risk=1")).atRisk).toBe(true);
  });

  it("at_risk and priority filter the rows", async () => {
    const rows = await withRisk(createMockAdapter(null)).listCases();
    const risky = filterWorklist(rows, { ...DEFAULT_FILTERS, atRisk: true });
    expect(risky.length).toBeGreaterThan(0);
    expect(risky.every((c) => c.at_risk && c.state !== "closed")).toBe(true);
    expect(filterWorklist(rows, { ...DEFAULT_FILTERS, priority: "urgent" }).every((c) => c.priority?.tier === "urgent")).toBe(true);
  });

  it("opening /coordinator?at_risk=1 shows only at-risk cases with the box ticked; changing a filter writes the URL back", async () => {
    const api = withRisk(createMockAdapter(null, { automation: true }));
    const all = (await api.listCases()).filter((c) => c.state !== "closed");
    const risky = all.filter((c) => c.at_risk);
    expect(risky.length).toBeLessThan(all.length);
    renderWithApp(<CoordinatorPage />, { api, route: "/coordinator?at_risk=1", path: "/coordinator" });
    await waitFor(() => expect(screen.getAllByTestId("worklist-row")).toHaveLength(risky.length));
    expect((screen.getByLabelText("At risk of being lost") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByLabelText("At risk of being lost"));
    await waitFor(() => expect(screen.getAllByTestId("worklist-row")).toHaveLength(all.length));
    fireEvent.change(within(screen.getByRole("form", { name: "Worklist filters" })).getByLabelText("Priority"), { target: { value: "urgent" } });
    await waitFor(() => expect((screen.getByLabelText("Priority") as HTMLSelectElement).value).toBe("urgent"));
  });
});
