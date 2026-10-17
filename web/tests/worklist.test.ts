import { describe, expect, it } from "vitest";
import type { CaseSummary } from "../src/api/contract";
import { DEFAULT_FILTERS, UNOWNED, filterWorklist, ownersOf, sortWorklist, worklist } from "../src/lib/worklist";

const c = (id: string, o: Partial<CaseSummary> = {}): CaseSummary => ({
  case_id: id, pathway: "colorectal_fit", state: "acknowledged",
  created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
  owner: null, due_at: null, overdue: false, escalation_level: 0, consent_frozen: false,
  last_event_at: "2026-10-01T00:00:00Z", risk_score: 0.1, ...o,
});
const NOW = "2026-11-04T09:00:00.000Z";
/** A case that is `days` days past its due date at NOW. */
const late = (days: number): Partial<CaseSummary> => ({
  overdue: true, due_at: new Date(new Date(NOW).getTime() - days * 86_400_000).toISOString(),
});

describe("sortWorklist", () => {
  it("orders by overdue days desc, then risk desc, then oldest first", () => {
    const rows = [
      c("a", { risk_score: 0.9 }),
      c("b", { ...late(5), risk_score: 0.1 }),
      c("c", { ...late(5), risk_score: 0.8 }),
      c("d", { ...late(9), risk_score: 0.0 }),
      c("e", { risk_score: 0.9, created_at: "2026-09-01T00:00:00Z" }),
    ];
    expect(sortWorklist(rows, NOW).map((r) => r.case_id)).toEqual(["d", "c", "b", "e", "a"]);
  });
  it("computes days overdue from due_at and the sim clock, not from a server field", () => {
    const rows = [c("recent", late(2)), c("old", late(20))];
    expect(sortWorklist(rows, NOW).map((r) => r.case_id)).toEqual(["old", "recent"]);
    // Same rows, a later sim clock: the ordering follows due_at, so it is unchanged but the gap grows.
    expect(sortWorklist(rows, "2026-12-31T00:00:00Z").map((r) => r.case_id)).toEqual(["old", "recent"]);
  });
  it("puts overdue cases first even when they are under a day late, and tolerates a missing risk score", () => {
    const rows = [c("fresh", { risk_score: undefined }), c("barely", { overdue: true, due_at: "2026-11-04T08:00:00Z", risk_score: undefined })];
    const copy = [...rows];
    expect(sortWorklist(rows, NOW)[0].case_id).toBe("barely");
    expect(rows).toEqual(copy);
  });
});

describe("filterWorklist", () => {
  const rows = [
    c("open-overdue", { owner: "Sara", ...late(3) }),
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
    expect(worklist(rows, { ...DEFAULT_FILTERS, state: "all" }, NOW)[0].case_id).toBe("open-overdue");
  });
  it("lists distinct owners alphabetically", () => {
    expect(ownersOf(rows)).toEqual(["Omar", "Sara"]);
  });
});
