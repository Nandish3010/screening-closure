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
  // A34 (ADR-042): a cancelled appointment returns the case to patient_acknowledged; `book_confirmatory` rebooks.
  { from: "confirmatory_booked", event: "appointment_cancelled", to: "patient_acknowledged" },
  // A33 (ADR-042): informed refusal, then a physician who is not the counsellor signs a declined closure.
  { from: "owner_accepted", event: "record_informed_refusal", to: "declined_pending_signoff", requires: ["counselling_ref", "counselled_by", "reason"] },
  { from: "patient_acknowledged", event: "record_informed_refusal", to: "declined_pending_signoff", requires: ["counselling_ref", "counselled_by", "reason"] },
  { from: "confirmatory_booked", event: "record_informed_refusal", to: "declined_pending_signoff", requires: ["counselling_ref", "counselled_by", "reason"] },
  { from: "declined_pending_signoff", event: "reassign", to: "declined_pending_signoff", requires: ["owner"] },
  { from: "declined_pending_signoff", event: "close", to: "closed", requires: ["evidence_ref", "signed_by"] },
];

/** States from which an informed refusal may be recorded (A33). */
export const REFUSAL_FROM_STATES: readonly string[] = ["owner_accepted", "patient_acknowledged", "confirmatory_booked"];
/** Calendar months from a recorded refusal to its re-offer date (`informed_refusal.re_offer_months`, assumed 12; H-11 is open). */
export const RE_OFFER_MONTHS = 12;

/** States from which a rejected (or bounced) handoff sends the case to `referral_rejected`. */
export const REFERRAL_STATES: readonly string[] = ["patient_acknowledged", "confirmatory_booked"];

/** Working days a bounced handoff request waits for an answer (`handoff_response_working_days`). */
export const HANDOFF_RESPONSE_WORKING_DAYS = 3;

/**
 * Working days allowed to perform the event (slas_working_days in the YAML). Each clock starts when the
 * state is entered. The pre-referral SLAs (acknowledge, accept_ownership, contact_patient, patient_acknowledge)
 * sum to 11 working days so they fit inside the 15-working-day referral milestone (A18). There is no per-state
 * SLA for complete_confirmatory: its clock is the `confirmatory_completed` milestone below. The UI takes due
 * dates from the API (`due_at`, `milestones`), not from this table, so it is data only (the mock adapter uses it).
 */
export const SLA_WORKING_DAYS: Record<string, number> = {
  acknowledge: 2,
  accept_ownership: 1,
  contact_patient: 3,
  patient_acknowledge: 5,
  book_confirmatory: 5,
  clinician_review: 5,
  close: 3, // sign-off after review (A13)
  reassign: 2, // re-route of a rejected or bounced referral (A5)
};

export type MilestoneName = "referral_requested" | "confirmatory_completed";

export interface MilestoneDef {
  name: MilestoneName;
  /** The event that starts the clock (`case_created` = the result release). The FIRST occurrence counts and the clock never restarts (A25). */
  from_event: string;
  /** The event that meets the milestone. */
  until_event: string;
  working_days: number;
  /** Row label on the case page. */
  label: string;
  /** Where the standard comes from (the YAML's `source`). */
  source: string;
}

/**
 * `milestones` in the pathway YAML (A18, ADR-028): clocks that span states, measured against a national standard.
 * The 31 working days come from the National Guideline's Appendix 2 KPI table, not from the IFHAS standard. They run from
 * the FIRST referral request (`handoff_requested`) and never restart (A25, ADR-028 amended): a bounced or re-routed referral
 * does not hand the case a fresh 31 days. The latest acceptance is only `referral_accepted_at`, for reporting.
 */
export const MILESTONES: readonly MilestoneDef[] = [
  {
    name: "referral_requested", from_event: "case_created", until_event: "handoff_requested", working_days: 15,
    label: "Referral within 15 working days of result",
    source: "DoH IFHAS standard July 2025 via docs/evidence.md section 8",
  },
  {
    name: "confirmatory_completed", from_event: "handoff_requested", until_event: "complete_confirmatory", working_days: 31,
    label: "Colonoscopy within 31 working days of referral",
    source: "National Guideline Appendix 2 via evidence.md",
  },
];

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
