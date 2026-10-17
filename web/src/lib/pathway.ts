/** Pathway knowledge the UI needs (mirrors config/pathways/colorectal_fit.yaml). */
import type { CaseState, TransitionEvent } from "../api/contract";
import { MAIN_STATES } from "../api/contract";

export const TRANSITIONS: { from: CaseState; event: TransitionEvent; to: CaseState; requires?: string[] }[] = [
  { from: "result_received", event: "acknowledge", to: "acknowledged" },
  { from: "acknowledged", event: "accept_ownership", to: "owner_accepted" },
  { from: "owner_accepted", event: "contact_patient", to: "patient_contacted" },
  { from: "patient_contacted", event: "patient_acknowledge", to: "patient_acknowledged" },
  { from: "patient_acknowledged", event: "book_confirmatory", to: "confirmatory_booked" },
  { from: "confirmatory_booked", event: "complete_confirmatory", to: "confirmatory_completed" },
  { from: "confirmatory_completed", event: "clinician_review", to: "clinician_reviewed" },
  { from: "clinician_reviewed", event: "close", to: "closed", requires: ["evidence_ref", "signed_by"] },
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

export const ESCALATION_LADDER = [
  { after_breach_days: 0, notify: "owner", action: "reminder" },
  { after_breach_days: 3, notify: "owner_supervisor", action: "escalate" },
  { after_breach_days: 7, notify: "programme_coordinator", action: "escalate" },
];

export function nextTransition(state: string) {
  return TRANSITIONS.find((t) => t.from === state);
}

export function transitionFor(state: string, event: string) {
  return TRANSITIONS.find((t) => t.from === state && t.event === event);
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

export const NOTIFY_LABEL: Record<string, string> = {
  owner: "Owner",
  owner_supervisor: "Owner's supervisor",
  programme_coordinator: "Programme coordinator",
};
