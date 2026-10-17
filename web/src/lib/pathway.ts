/** Pathway knowledge the UI needs (mirrors config/pathways/colorectal_fit.yaml). */
import type { CaseState, EscalationRung, HandoffRejectReason } from "../api/contract";
import { MAIN_STATES } from "../api/contract";

type Transition = { from: CaseState; event: string; to: CaseState; requires?: string[] };

export const TRANSITIONS: Transition[] = [
  { from: "result_received", event: "acknowledge", to: "acknowledged" },
  { from: "acknowledged", event: "accept_ownership", to: "owner_accepted" },
  { from: "owner_accepted", event: "contact_patient", to: "patient_contacted" },
  { from: "patient_contacted", event: "patient_acknowledge", to: "patient_acknowledged" },
  { from: "patient_acknowledged", event: "book_confirmatory", to: "confirmatory_booked" },
  { from: "confirmatory_booked", event: "complete_confirmatory", to: "confirmatory_completed" },
  { from: "confirmatory_completed", event: "clinician_review", to: "clinician_reviewed" },
  { from: "clinician_reviewed", event: "close", to: "closed", requires: ["evidence_ref", "signed_by"] },
];

/**
 * Side transitions the MOCK implements so the reopen flow is demonstrable. The real state effect of
 * `reopen` is the backend's call; the UI renders whatever `state` the server returns.
 * ASSUMPTION (mock only): closed --reopen--> reopened --accept_ownership--> owner_accepted.
 */
export const SIDE_TRANSITIONS: Transition[] = [
  { from: "closed", event: "reopen", to: "reopened", requires: ["reason"] },
  { from: "reopened", event: "accept_ownership", to: "owner_accepted" },
];

/** Working days allowed to perform the event (slas_working_days in the YAML). */
export const SLA_WORKING_DAYS: Record<string, number> = {
  acknowledge: 2,
  accept_ownership: 2,
  contact_patient: 5,
  patient_acknowledge: 7,
  book_confirmatory: 14,
  complete_confirmatory: 45,
  clinician_review: 5,
};

/** escalation_ladder from the pathway YAML, in the event vocabulary (`escalate {rung, target_role}`). */
export const ESCALATION_LADDER: { after_breach_days: number; rung: EscalationRung; target_role: string }[] = [
  { after_breach_days: 0, rung: "reminder", target_role: "owner" },
  { after_breach_days: 3, rung: "supervisor", target_role: "supervisor" },
  { after_breach_days: 7, rung: "coordinator", target_role: "coordinator" },
];

/** escalation_level on the read model: 0 = none, else 1 + index of the rung. */
export function rungLevel(rung: string): 0 | 1 | 2 | 3 {
  const i = ESCALATION_LADDER.findIndex((r) => r.rung === rung);
  return (i + 1) as 0 | 1 | 2 | 3;
}

const ALL_TRANSITIONS = [...TRANSITIONS, ...SIDE_TRANSITIONS];

export function nextTransition(state: string) {
  return ALL_TRANSITIONS.find((t) => t.from === state);
}

export function transitionFor(state: string, event: string) {
  return ALL_TRANSITIONS.find((t) => t.from === state && t.event === event);
}

export function stateLabel(state: string): string {
  return state.replace(/_/g, " ");
}

export function stateIndex(state: string): number {
  return (MAIN_STATES as readonly string[]).indexOf(state);
}

/** Add working days (Mon-Fri; UAE weekend since 2022 is Sat-Sun) to an ISO timestamp. */
export function addWorkingDays(startIso: string, days: number): string {
  const d = new Date(startIso);
  let left = days;
  while (left > 0) {
    d.setUTCDate(d.getUTCDate() + 1);
    const dow = d.getUTCDay();
    if (dow !== 0 && dow !== 6) left -= 1;
  }
  return d.toISOString();
}

const MS_DAY = 86_400_000;
export function wholeDaysBetween(fromIso: string, toIso: string): number {
  return Math.floor((new Date(toIso).getTime() - new Date(fromIso).getTime()) / MS_DAY);
}

export const RUNG_LABEL: Record<EscalationRung, string> = {
  reminder: "Reminder to the owner",
  supervisor: "Owner's supervisor",
  coordinator: "Programme coordinator",
};

export const HANDOFF_REASON_LABEL: Record<HandoffRejectReason, string> = {
  capacity: "No capacity (no slot in time)",
  wrong_service: "Wrong service for this patient",
  missing_info: "Missing information in the referral",
  patient_declined: "Patient declined",
};

/** Human label for a hand-off rejection reason; unknown codes are shown as-is (never hidden). */
export function handoffReasonLabel(reason: string | undefined): string {
  if (!reason) return "No reason given";
  return HANDOFF_REASON_LABEL[reason as HandoffRejectReason] ?? reason;
}
