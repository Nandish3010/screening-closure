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
 * Side transitions the MOCK implements (the YAML's `side_states`), so reopen and a rejected referral are
 * demonstrable. The UI renders whatever `state` the server returns.
 *  closed --reopen--> reopened --clinician_review--> clinician_reviewed
 *  referral_rejected --reassign | handoff_accepted--> owner_accepted
 * A `handoff_rejected` moves a case to `referral_rejected` only from the referral states (see nextState in
 * derive.ts); from any other open state it is an annotation. Consent withdrawal does not change the mock's
 * state (it sets the frozen flag); the real engine moves the case to `consent_withdrawn` and remembers the
 * state it came from.
 */
export const SIDE_TRANSITIONS: Transition[] = [
  { from: "closed", event: "reopen", to: "reopened", requires: ["reason"] },
  { from: "reopened", event: "clinician_review", to: "clinician_reviewed" },
  { from: "referral_rejected", event: "reassign", to: "owner_accepted", requires: ["owner"] },
  { from: "referral_rejected", event: "handoff_accepted", to: "owner_accepted" },
];

/** States from which a rejected (or bounced) handoff sends the case to `referral_rejected`. */
export const REFERRAL_STATES: readonly string[] = ["patient_acknowledged", "confirmatory_booked"];

/** Working days a bounced handoff request waits for an answer (`handoff_response_working_days`). */
export const HANDOFF_RESPONSE_WORKING_DAYS = 3;

/**
 * Working days allowed to perform the event (slas_working_days in the YAML). Each clock starts when the
 * state is entered, except complete_confirmatory (31), which the API counts from the latest referral
 * acceptance (`sla_clock_start` in the YAML, A13). The UI takes due dates from the API (`due_at`), not
 * from this table; only the mock uses it.
 */
export const SLA_WORKING_DAYS: Record<string, number> = {
  acknowledge: 2,
  accept_ownership: 2,
  contact_patient: 5,
  patient_acknowledge: 7,
  book_confirmatory: 14,
  complete_confirmatory: 31,
  clinician_review: 5,
  close: 3, // sign-off after review (A13)
  reassign: 2, // re-route of a rejected or bounced referral (A5)
};

/** SLAs whose clock starts at the latest `handoff_accepted` (when there was one) instead of state entry. */
export const SLA_CLOCK_FROM_HANDOFF: readonly string[] = ["complete_confirmatory"];

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
