/** Funnel shares on a tiny n: "1 of 1" is not "100%". */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { FunnelCard, NO_FUNNEL_FILTER } from "../src/components/FunnelCard";
import { funnelMetricsOf } from "../src/lib/funnel";
import type { CaseEvent } from "../src/api/contract";

const day = (n: number) => new Date(Date.parse("2026-09-01T09:00:00.000Z") + n * 86_400_000).toISOString();
const ev = (type: string, d: number, actor = "coordinator:m"): CaseEvent => ({ seq: 1, type, at: day(d), actor, payload: {} });
const one = [ev("case_created", 0, "system:intake"), ev("acknowledge", 1), ev("accept_ownership", 2), ev("contact_patient", 3), ev("patient_acknowledge", 4), ev("book_confirmatory", 6)];

describe("funnel on a handful of cases", () => {
  it("says n=1, too few to compare, instead of a percentage", () => {
    const data = funnelMetricsOf([{ id: "a", events: one, pathway: "colorectal_fit", priority: null }], "2026-11-04T09:00:00.000Z", {}, ["colorectal_fit"]);
    render(<MemoryRouter><FunnelCard data={data} filters={NO_FUNNEL_FILTER} onFilters={() => undefined} error={null} loading={false} /></MemoryRouter>);
    const cell = screen.getByTestId("funnel-conversion-referred");
    expect(cell.textContent).toBe("n=1, too few to compare");
    expect(cell.textContent).not.toMatch(/%/);
  });
});
