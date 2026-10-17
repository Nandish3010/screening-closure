/** Derivations shared by the mock adapter and the UI (state, owner, SLA clock, handoffs, consent, drafts). */
import type { CaseEvent, CaseSummary, ContactChannel, Lang, TimelineItem } from "../api/contract";
import { ApiError } from "../api/contract";
import { SLA_WORKING_DAYS, addWorkingDays, nextTransition, transitionFor, wholeDaysBetween } from "./pathway";

export const INITIAL_STATE = "result_received";

/** Replays events exactly like the engine: only transition events change state. */
export function replayState(events: CaseEvent[]): string {
  let state = INITIAL_STATE;
  for (const ev of events) {
    const t = transitionFor(state, ev.type);
    if (t) state = t.to;
  }
  return state;
}

/** Timestamp at which the case entered its current state. */
export function stateEnteredAt(events: CaseEvent[]): string {
  let state = INITIAL_STATE;
  let at = events[0]?.at ?? new Date(0).toISOString();
  for (const ev of events) {
    const t = transitionFor(state, ev.type);
    if (t) {
      state = t.to;
      at = ev.at;
    }
  }
  return at;
}

export function currentOwner(events: CaseEvent[]): { name: string; role?: string } | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.type === "accept_ownership" && typeof ev.payload.owner === "string") {
      return { name: ev.payload.owner, role: typeof ev.payload.owner_role === "string" ? ev.payload.owner_role : undefined };
    }
  }
  return null;
}

export function slaClock(events: CaseEvent[], state: string, simNow: string) {
  const next = nextTransition(state);
  const days = next ? SLA_WORKING_DAYS[next.event] : undefined;
  if (!next || days === undefined) return { sla_due_at: null as string | null, overdue_days: 0, sla_event: next?.event ?? null };
  const due = addWorkingDays(stateEnteredAt(events), days);
  return { sla_due_at: due, overdue_days: Math.max(0, wholeDaysBetween(due, simNow)), sla_event: next.event };
}

export function summarise(
  caseId: string,
  events: CaseEvent[],
  simNow: string,
  risk: number,
): CaseSummary {
  const state = replayState(events);
  const clock = slaClock(events, state, simNow);
  return {
    case_id: caseId,
    pathway: "colorectal_fit",
    state,
    created_at: events[0]?.at ?? simNow,
    updated_at: events[events.length - 1]?.at ?? simNow,
    owner: currentOwner(events)?.name ?? null,
    sla_due_at: clock.sla_due_at,
    overdue_days: clock.overdue_days,
    risk_score: risk,
  };
}

/** Mirrors api/app/state_machine.apply for the mock. Throws ApiError(409) like the real API. */
export function validateAppend(state: string, type: string, actor: string, payload: Record<string, unknown>) {
  const nonTransition = [
    "hand_off", "hand_off_accepted", "hand_off_rejected", "escalate", "callback_requested",
    "consent_updated", "draft_generated", "draft_approved",
  ];
  if (nonTransition.includes(type)) {
    if (state === "closed") throw new ApiError(409, "case is closed");
    return;
  }
  const t = transitionFor(state, type);
  if (!t) throw new ApiError(409, `event '${type}' not allowed from state '${state}'`);
  if (t.to === "closed" && /^(system:|scheduler|watchdog|model:)/.test(actor)) {
    throw new ApiError(409, "automated actors may not close a case");
  }
  for (const key of t.requires ?? []) {
    if (!payload[key]) throw new ApiError(409, `event '${type}' requires non-empty '${key}'`);
  }
}

// ---- Views derived from the log, used by the case detail page and the patient page -----

export interface HandoffView {
  provider: string;
  status: "requested" | "accepted" | "rejected";
  reason?: string;
  at: string;
}

/** One entry per hand_off request, resolved by the next accepted/rejected for that provider. */
export function handoffs(events: CaseEvent[]): HandoffView[] {
  const out: HandoffView[] = [];
  for (const ev of events) {
    const p = String(ev.payload.provider ?? ev.payload.to_provider ?? "");
    if (ev.type === "hand_off") out.push({ provider: p, status: "requested", at: ev.at });
    else if (ev.type === "hand_off_accepted" || ev.type === "hand_off_rejected") {
      const open = [...out].reverse().find((h) => h.provider === p && h.status === "requested");
      const rejected = ev.type === "hand_off_rejected";
      const reason = typeof ev.payload.reason === "string" ? ev.payload.reason : undefined;
      if (open) {
        open.status = rejected ? "rejected" : "accepted";
        open.reason = reason;
        open.at = ev.at;
      } else out.push({ provider: p, status: rejected ? "rejected" : "accepted", reason, at: ev.at });
    }
  }
  return out;
}

export interface ConsentView {
  channel: ContactChannel | null;
  share_with_specialist: boolean;
  updated_at: string | null;
}

export function consentState(events: CaseEvent[]): ConsentView {
  let view: ConsentView = { channel: null, share_with_specialist: false, updated_at: null };
  for (const ev of events) {
    if (ev.type === "consent_updated") {
      view = {
        channel: (ev.payload.channel as ContactChannel) ?? view.channel,
        share_with_specialist: Boolean(ev.payload.share_with_specialist),
        updated_at: ev.at,
      };
    }
  }
  return view;
}

export interface DraftView {
  draft_id: string;
  provider: string;
  fallback_used: boolean;
  text_ar?: string;
  text_en?: string;
  generated_at: string;
  generated_by: string;
  approved_by?: string;
  approved_at?: string;
}

/** AI-draft provenance: who/what produced each draft, and who approved it. */
export function drafts(events: CaseEvent[]): DraftView[] {
  const out: DraftView[] = [];
  for (const ev of events) {
    if (ev.type === "draft_generated") {
      out.push({
        draft_id: String(ev.payload.draft_id),
        provider: String(ev.payload.provider ?? "unknown"),
        fallback_used: Boolean(ev.payload.fallback_used),
        text_ar: typeof ev.payload.text_ar === "string" ? ev.payload.text_ar : undefined,
        text_en: typeof ev.payload.text_en === "string" ? ev.payload.text_en : undefined,
        generated_at: ev.at,
        generated_by: ev.actor,
      });
    } else if (ev.type === "draft_approved") {
      const d = out.find((x) => x.draft_id === String(ev.payload.draft_id));
      if (d) {
        d.approved_by = String(ev.payload.approved_by ?? ev.actor);
        d.approved_at = ev.at;
      }
    }
  }
  return out;
}

export function rejectionReasons(events: CaseEvent[]): { provider: string; reason: string; at: string }[] {
  return handoffs(events)
    .filter((h) => h.status === "rejected")
    .map((h) => ({ provider: h.provider, reason: h.reason ?? "No reason given", at: h.at }));
}

export function hasCallbackPending(events: CaseEvent[]): boolean {
  let pending = false;
  for (const ev of events) {
    if (ev.type === "callback_requested") pending = true;
    if (ev.type === "contact_patient") pending = false;
  }
  return pending;
}

export function patientLang(events: CaseEvent[]): Lang | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const l = events[i].payload.lang;
    if (l === "ar" || l === "en") return l;
  }
  return null;
}

/** Item key helper for React lists. */
export function itemKey(it: TimelineItem, i: number): string {
  return `${it.kind}-${it.at}-${i}`;
}
