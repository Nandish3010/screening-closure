/**
 * Human effort versus automation (A21), computed from the event log. Used by the mock adapter (the real API reports
 * `effort` on the case and `GET /metrics/effort` itself) and by the tests.
 *
 * THE MINUTES ARE ASSUMED, NOT MEASURED. Each action has a minutes figure below that someone made up to size the
 * programme target; they are to be replaced by a time-and-motion source. Every place that shows a minutes or a
 * percentage figure says so.
 */
import type { CaseEffort, CaseEvent, EffortMetrics, EffortPerCase } from "../api/contract";
import { actorKindOf, actorRole } from "./derive";

/** Assumed minutes a person spends per kind of action. */
export const ASSUMED_MINUTES = {
  acknowledge: 2,
  owner_assignment: 2,
  accept_ownership: 1,
  manual_message: 3,
  evidence_chase: 5,
  closure_preparation: 2,
  phone_call: 6,
  booking: 5,
  referral_handling: 8,
  review: 3,
  sign_off: 4,
  escalation_handling: 5,
  message_approval: 2,
} as const;
export type EffortAction = keyof typeof ASSUMED_MINUTES;

export const EFFORT_TARGET_PCT = 40;
export const EFFORT_ASSUMPTION_NOTE =
  "Minutes per action are assumed, not measured. They are an estimate to be sourced (for example from a time-and-motion study) before the percentage is quoted as a result.";

/** The manual action a human event stands for, or null for an event that costs no staff time (patient, service, model). */
function humanAction(ev: CaseEvent): EffortAction | null {
  switch (ev.type) {
    case "acknowledge": return "acknowledge";
    case "assign_ownership": return "owner_assignment";
    case "accept_ownership": return "accept_ownership";
    case "contact_patient": return ev.payload.channel === "phone" ? "phone_call" : "manual_message";
    case "message_sent": return "manual_message";
    case "book_confirmatory": return "booking";
    case "handoff_requested": case "reassign": return "referral_handling";
    case "complete_confirmatory": return "evidence_chase";
    case "closure_proposed": return "closure_preparation";
    case "close": return "sign_off";
    case "escalate": return "escalation_handling";
    case "message_approved": case "message_rejected": return "message_approval";
    case "clinician_review": case "handoff_accepted": case "handoff_rejected": case "escalation_accepted":
    case "consent_updated": case "mark_lost_contact": case "reopen": case "callback_requested":
      return "review";
    default: return null;
  }
}

/** The manual action an automated event replaced; null when it replaced none (a flag for a human is not saved work). */
function replacedAction(ev: CaseEvent): EffortAction | null {
  return ev.type === "review_requested" ? null : humanAction(ev);
}

const round1 = (n: number) => Math.round(n * 10) / 10;

/** Effort on one case: staff human events, events by `system:automation`, and the assumed minutes behind them. */
export function effortOf(events: CaseEvent[]): CaseEffort {
  let human = 0;
  let automated = 0;
  let humanMin = 0;
  let replacedMin = 0;
  for (const ev of events) {
    const kind = ev.actor_kind ?? actorKindOf(ev.actor);
    if (kind === "automation") {
      automated += 1;
      const a = replacedAction(ev);
      if (a) replacedMin += ASSUMED_MINUTES[a];
    } else if (kind === "human" && actorRole(ev.actor) !== "patient") {
      human += 1;
      const a = humanAction(ev);
      if (a) humanMin += ASSUMED_MINUTES[a];
    }
  }
  return shape(human, automated, humanMin, humanMin + replacedMin);
}

function shape(human: number, automated: number, humanMin: number, baseline: number): CaseEffort {
  const total = human + automated;
  return {
    human_actions: human,
    automated_actions: automated,
    automation_share: total ? round1((automated / total) * 100) / 100 : null,
    human_minutes: humanMin,
    baseline_minutes: baseline,
    minutes_saved: baseline - humanMin,
    reduction_pct: baseline ? round1(((baseline - humanMin) / baseline) * 100) : null,
  };
}

/** `GET /metrics/effort` over a set of cases (the mock's answer). */
export function effortMetricsOf(cases: Record<string, CaseEvent[]>, simTime: string): EffortMetrics {
  const ids = Object.keys(cases);
  const per: EffortPerCase[] = [];
  let human = 0, automated = 0, humanMin = 0, baseline = 0;
  for (const id of ids) {
    const e = effortOf(cases[id]);
    per.push({
      case_id: id, human_actions: e.human_actions, automated_actions: e.automated_actions,
      automation_share: e.automation_share, human_minutes: e.human_minutes, baseline_minutes: e.baseline_minutes,
    });
    human += e.human_actions;
    automated += e.automated_actions;
    humanMin += e.human_minutes;
    baseline += e.baseline_minutes;
  }
  const t = shape(human, automated, humanMin, baseline);
  return {
    sim_time: simTime,
    cases: ids.length,
    totals: { ...t, target_pct: EFFORT_TARGET_PCT, meets_target: t.reduction_pct !== null && t.reduction_pct >= EFFORT_TARGET_PCT },
    per_case: per,
    assumptions: { status: "assumed", note: EFFORT_ASSUMPTION_NOTE, minutes: { ...ASSUMED_MINUTES } },
  };
}
