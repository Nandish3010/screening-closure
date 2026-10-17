import { describe, expect, it } from "vitest";
import type { CaseSummary } from "../src/api/contract";
import { DEFAULT_FILTERS, UNOWNED, filterWorklist, ownersOf, sortWorklist, worklist } from "../src/lib/worklist";

const c = (id: string, o: Partial<CaseSummary> = {}): CaseSummary => ({
  case_id: id, pathway: "colorectal_fit", state: "acknowledged",
  created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
  owner: null, overdue_days: 0, risk_score: 0.1, ...o,
});

describe("sortWorklist", () => {
  it("orders by overdue days desc, then risk desc, then oldest first", () => {
    const rows = [
      c("a", { overdue_days: 0, risk_score: 0.9 }),
      c("b", { overdue_days: 5, risk_score: 0.1 }),
      c("c", { overdue_days: 5, risk_score: 0.8 }),
      c("d", { overdue_days: 9, risk_score: 0.0 }),
      c("e", { overdue_days: 0, risk_score: 0.9, created_at: "2026-09-01T00:00:00Z" }),
    ];
    expect(sortWorklist(rows).map((r) => r.case_id)).toEqual(["d", "c", "b", "e", "a"]);
  });
  it("treats missing overdue/risk as zero and does not mutate the input", () => {
    const rows = [c("x", { overdue_days: undefined, risk_score: undefined }), c("y", { overdue_days: 1 })];
    const copy = [...rows];
    expect(sortWorklist(rows)[0].case_id).toBe("y");
    expect(rows).toEqual(copy);
  });
});

describe("filterWorklist", () => {
  const rows = [
    c("open-overdue", { owner: "Sara", overdue_days: 3 }),
    c("open-ok", { owner: "Omar" }),
    c("unowned", { owner: null, state: "result_received" }),
    c("closed", { state: "closed", owner: "Sara" }),
  ];
  it("hides closed cases by default", () => {
    expect(filterWorklist(rows, DEFAULT_FILTERS).map((r) => r.case_id)).toEqual(["open-overdue", "open-ok", "unowned"]);
  });
  it("filters by state, owner, unowned and overdue", () => {
    expect(filterWorklist(rows, { ...DEFAULT_FILTERS, state: "all" })).toHaveLength(4);
    expect(filterWorklist(rows, { ...DEFAULT_FILTERS, state: "result_received" }).map((r) => r.case_id)).toEqual(["unowned"]);
    expect(filterWorklist(rows, { ...DEFAULT_FILTERS, owner: "Omar" }).map((r) => r.case_id)).toEqual(["open-ok"]);
    expect(filterWorklist(rows, { ...DEFAULT_FILTERS, owner: UNOWNED }).map((r) => r.case_id)).toEqual(["unowned"]);
    expect(filterWorklist(rows, { ...DEFAULT_FILTERS, overdueOnly: true }).map((r) => r.case_id)).toEqual(["open-overdue"]);
  });
  it("combines filtering and sorting", () => {
    expect(worklist(rows, { ...DEFAULT_FILTERS, state: "all" })[0].case_id).toBe("open-overdue");
  });
  it("lists distinct owners alphabetically", () => {
    expect(ownersOf(rows)).toEqual(["Omar", "Sara"]);
  });
});
