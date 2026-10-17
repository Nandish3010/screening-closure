import type { CaseSummary } from "../api/contract";

export interface WorklistFilters {
  /** "open" (default) = everything not closed; "all"; or an exact state. */
  state: string;
  /** "" = anyone; "__unowned__" = no owner; else exact owner name. */
  owner: string;
  overdueOnly: boolean;
}

export const DEFAULT_FILTERS: WorklistFilters = { state: "open", owner: "", overdueOnly: false };
export const UNOWNED = "__unowned__";

/**
 * Most overdue first, then highest risk score, then oldest case (stable, deterministic).
 * The risk score ONLY orders the list; it never triggers any action.
 */
export function sortWorklist(cases: CaseSummary[]): CaseSummary[] {
  return [...cases].sort((a, b) => {
    const od = (b.overdue_days ?? 0) - (a.overdue_days ?? 0);
    if (od !== 0) return od;
    const rk = (b.risk_score ?? 0) - (a.risk_score ?? 0);
    if (rk !== 0) return rk;
    const created = a.created_at.localeCompare(b.created_at);
    return created !== 0 ? created : a.case_id.localeCompare(b.case_id);
  });
}

export function filterWorklist(cases: CaseSummary[], f: WorklistFilters): CaseSummary[] {
  return cases.filter((c) => {
    if (f.state === "open" ? c.state === "closed" : f.state !== "all" && c.state !== f.state) return false;
    if (f.owner === UNOWNED ? !!c.owner : f.owner !== "" && c.owner !== f.owner) return false;
    if (f.overdueOnly && (c.overdue_days ?? 0) <= 0) return false;
    return true;
  });
}

export const worklist = (cases: CaseSummary[], f: WorklistFilters) => sortWorklist(filterWorklist(cases, f));

export function ownersOf(cases: CaseSummary[]): string[] {
  return [...new Set(cases.map((c) => c.owner).filter((o): o is string => !!o))].sort();
}
