/**
 * The mock's stand-in for the hosted demo's judge controls (ADR-057), switched on with `?demo=1` (see `mockDemoFromEnv`). It mirrors
 * what api/app/demo_controls.py does so the guided path can be walked, tested and recorded without the stack: a fresh case per step
 * per browser session, a reset that restores the seed, and a missed follow-up whose events the mock "engine" (the scheduler actor)
 * writes. The mock's clock does not move; the real API's does.
 */
import type { CaseSummary, DemoGuide, GuideStepId, GuidedCase } from "./contract";
import { GUIDE_STEPS } from "./contract";

const WANTS: Record<GuideStepId, (c: CaseSummary) => boolean> = {
  record: (c) => ["result_received", "acknowledged", "owner_accepted"].includes(c.state) && c.escalation_level === 0,
  prioritise: (c) => c.priority?.tier === "urgent",
  refer: (c) => !!c.insurance && ["owner_accepted", "patient_contacted", "patient_acknowledged"].includes(c.state) && !c.pending_handoff,
  remind: (c) => ["patient_contacted", "patient_acknowledged", "confirmatory_booked"].includes(c.state) && c.escalation_level === 0,
  outcome: (c) => ["confirmatory_completed", "clinician_reviewed"].includes(c.state),
  safety: (c) => !!c.due_at && !c.overdue && c.escalation_level === 0 && !["closed", "confirmatory_completed", "clinician_reviewed"].includes(c.state),
  privacy: (c) => c.state !== "closed",
};

/** One untouched case per step; a case already given to another session is skipped. `seed` is the event count of each case at reset. */
export function mockGuide(
  rows: CaseSummary[], seed: Record<string, number>, counts: Record<string, number>,
  claims: Map<string, string>, session: string,
): DemoGuide {
  const fresh = (c: CaseSummary) => seed[c.case_id] !== undefined && counts[c.case_id] <= seed[c.case_id];
  const used = new Set<string>();
  const steps = {} as Record<GuideStepId, GuidedCase | null>;
  for (const step of GUIDE_STEPS) {
    const pool = rows
      .filter((c) => WANTS[step](c) && (fresh(c) || step === "prioritise" || step === "privacy") && !used.has(c.case_id))
      .filter((c) => !claims.has(c.case_id) || claims.get(c.case_id) === session || !fresh(c))
      .sort((a, b) => (a.due_at ?? "9").localeCompare(b.due_at ?? "9") || a.case_id.localeCompare(b.case_id));
    const mine = pool.find((c) => claims.get(c.case_id) === session);
    const pick = mine ?? pool[0];
    if (!pick) { steps[step] = null; continue; }
    used.add(pick.case_id);
    if (fresh(pick)) claims.set(pick.case_id, session);
    steps[step] = { case_id: pick.case_id, state: pick.state, due_at: pick.due_at, priority: pick.priority?.tier ?? null, scheme: pick.insurance?.label ?? null, fresh: fresh(pick) };
  }
  return { steps };
}
