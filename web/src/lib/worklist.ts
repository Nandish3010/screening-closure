import type { CaseSummary } from "../api/contract";
import { overdueInfo } from "./derive";
import { DEFAULT_PATHWAY } from "./pathway";
import { priorityRank } from "./priority";

export interface WorklistFilters {
  /** "open" (default) = everything not closed; "all"; or an exact state. */
  state: string;
  /** "" = anyone; "__unowned__" = no owner; else exact owner name. */
  owner: string;
  overdueOnly: boolean;
  /** Only cases with an open watchdog flag ("nobody is looking at this"). */
  flaggedOnly: boolean;
  /** "" = every pathway; else the pathway name (`colorectal_fit`, `cervical_hpv`, `chronic_conditions`). */
  pathway: string;
}

export const DEFAULT_FILTERS: WorklistFilters = { state: "open", owner: "", overdueOnly: false, flaggedOnly: false, pathway: "" };
export const UNOWNED = "__unowned__";

/**
 * Result priority first (urgent, soon, routine; a case with no priority counts as routine, so a list with no priorities sorts
 * exactly as before), then overdue first, then most days overdue (computed from `due_at` and the sim clock `now`), then highest
 * risk score, then oldest case (stable, deterministic). The priority and the risk score ONLY order the list; neither triggers
 * any action, and the priority does not change a case's SLA clock.
 */
export function sortWorklist(cases: CaseSummary[], now?: string | null): CaseSummary[] {
  return [...cases].sort((a, b) => {
    const pr = priorityRank(a.priority) - priorityRank(b.priority);
    if (pr !== 0) return pr;
    const oa = overdueInfo(a, now);
    const ob = overdueInfo(b, now);
    if (oa.overdue !== ob.overdue) return oa.overdue ? -1 : 1;
    const od = ob.days - oa.days;
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
    if (f.overdueOnly && !c.overdue) return false;
    if (f.flaggedOnly && !c.watchdog_flagged) return false;
    if (f.pathway !== "" && (c.pathway || DEFAULT_PATHWAY) !== f.pathway) return false;
    return true;
  });
}

export const worklist = (cases: CaseSummary[], f: WorklistFilters, now?: string | null) =>
  sortWorklist(filterWorklist(cases, f), now);

export function ownersOf(cases: CaseSummary[]): string[] {
  return [...new Set(cases.map((c) => c.owner).filter((o): o is string => !!o))].sort();
}

/** The pathways present in a list of cases, default pathway first (what the filter offers). */
export function pathwaysOf(cases: CaseSummary[]): string[] {
  const seen = [...new Set(cases.map((c) => c.pathway || DEFAULT_PATHWAY))];
  return seen.sort((a, b) => (a === DEFAULT_PATHWAY ? -1 : b === DEFAULT_PATHWAY ? 1 : a.localeCompare(b)));
}
