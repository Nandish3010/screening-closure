/** Derivations shared by the mock adapter and the UI (state, owner, SLA clock, handoffs, consent, drafts). */
import type {
  CaseDetail, CaseEvent, CaseSummary, ContactChannel, EscalationRung, Lang, TimelineItem, WatchdogFlag,
} from "../api/contract";
import { ApiError } from "../api/contract";
import { SLA_WORKING_DAYS, addWorkingDays, nextTransition, rungLevel, transitionFor, wholeDaysBetween } from "./pathway";

export const INITIAL_STATE = "result_received";

// ---- Actors ("role:name", or a bare "watchdog") ------------------------------------------

const AUTOMATED_ROLES = new Set(["system", "model", "watchdog", "scheduler"]);

export function actorRole(actor: string): string {
  const i = actor.indexOf(":");
  return i < 0 ? actor : actor.slice(0, i);
}

/** The name part of `role:name`; "" for a bare actor such as `watchdog`. */
export function actorName(actor: string): string {
  const i = actor.indexOf(":");
  return i < 0 ? "" : actor.slice(i + 1);
}

/** Automated actors (scheduler, watchdog, models, system jobs) get an "automated" chip in the UI. */
export function isAutomatedActor(actor: string): boolean {
  return AUTOMATED_ROLES.has(actorRole(actor));
}

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
  if (!next || days === undefined) return { due_at: null as string | null, days_overdue: 0, sla_event: next?.event ?? null };
  const due = addWorkingDays(stateEnteredAt(events), days);
  return { due_at: due, days_overdue: Math.max(0, wholeDaysBetween(due, simNow)), sla_event: next.event };
}

/** Whole days past `due_at` as of `simNow`; 0 when not due yet or when there is no clock. */
export function overdueDays(dueAt: string | null | undefined, simNow: string | null | undefined): number {
  if (!dueAt || !simNow) return 0;
  return Math.max(0, wholeDaysBetween(dueAt, simNow));
}

/**
 * Overdue as the UI shows it: `overdue` from the read model, days computed from `due_at` and sim time.
 * An overdue case less than one day late reports overdue=true with days=0.
 */
export function overdueInfo(c: { due_at: string | null; overdue: boolean }, simNow: string | null | undefined) {
  return { overdue: c.overdue, days: c.overdue ? overdueDays(c.due_at, simNow) : 0 };
}

/** Timestamp of the latest state change (or case creation). */
function lastStateChangeIndex(events: CaseEvent[]): number {
  let state = INITIAL_STATE;
  let idx = 0;
  events.forEach((ev, i) => {
    const t = transitionFor(state, ev.type);
    if (t) {
      state = t.to;
      idx = i;
    }
  });
  return idx;
}

/** Events after the most recent state change. */
function sinceStateChange(events: CaseEvent[]): CaseEvent[] {
  return events.slice(lastStateChangeIndex(events) + 1);
}

/**
 * Latest `mark_overdue` that no later state change has superseded (the scheduler's overdue marker).
 * Used when the read model has no `overdue` flag.
 */
export function overdueMarker(events: CaseEvent[]): { state: string; due_at: string; at: string } | null {
  const marks = sinceStateChange(events).filter((e) => e.type === "mark_overdue");
  const m = marks.at(-1);
  if (!m) return null;
  return { state: String(m.payload.state ?? ""), due_at: String(m.payload.due_at ?? ""), at: m.at };
}

/**
 * Open watchdog flag: the latest `watchdog_flagged` with no later state change and no later event by a
 * human (a person looking at the case clears "nobody is looking at this"). UI heuristic.
 */
export function watchdogFlag(events: CaseEvent[]): WatchdogFlag | null {
  const since = sinceStateChange(events);
  let flag: WatchdogFlag | null = null;
  for (const ev of since) {
    if (ev.type === "watchdog_flagged") {
      const reason = ev.payload.reason === "scheduler_down" ? "scheduler_down" : "idle";
      flag = { reason, at: ev.at, ...(typeof ev.payload.idle_days === "number" ? { idle_days: ev.payload.idle_days } : {}) };
    } else if (flag && !isAutomatedActor(ev.actor)) {
      flag = null;
    }
  }
  return flag;
}

/** Highest escalation rung reached since the last state change: 0 none .. 3 coordinator. */
export function escalationLevel(events: CaseEvent[]): 0 | 1 | 2 | 3 {
  let level = 0;
  for (const ev of sinceStateChange(events)) {
    if (ev.type === "escalate") level = Math.max(level, rungLevel(String(ev.payload.rung)));
  }
  return level as 0 | 1 | 2 | 3;
}

export interface PendingEscalation {
  rung: EscalationRung | string;
  target_role: string;
  at: string;
}

/** The latest `escalate` that no human has accepted yet (`escalation_accepted` after it), or null. */
export function pendingEscalation(events: CaseEvent[]): PendingEscalation | null {
  let pending: PendingEscalation | null = null;
  for (const ev of events) {
    if (ev.type === "escalate") {
      pending = { rung: String(ev.payload.rung ?? ""), target_role: String(ev.payload.target_role ?? ""), at: ev.at };
    } else if (ev.type === "escalation_accepted") pending = null;
  }
  return pending;
}

/**
 * Consent is frozen when the latest `consent_updated` says the patient withdrew.
 * MOCK ASSUMPTION: payload `{withdrawn: true}`. Against the real backend the read model's
 * `consent_frozen` is authoritative and this is only the fallback.
 */
export function consentFrozen(events: CaseEvent[]): boolean {
  let frozen = false;
  for (const ev of events) if (ev.type === "consent_updated") frozen = ev.payload.withdrawn === true;
  return frozen;
}

/** True after `mark_lost_contact` until the patient is reached again (contact or acknowledgement). */
export function lostContact(events: CaseEvent[]): boolean {
  let lost = false;
  for (const ev of events) {
    if (ev.type === "mark_lost_contact") lost = true;
    else if (ev.type === "contact_patient" || ev.type === "patient_acknowledge") lost = false;
  }
  return lost;
}

export function summarise(
  caseId: string,
  events: CaseEvent[],
  simNow: string,
  risk: number,
): CaseSummary {
  const state = replayState(events);
  const clock = slaClock(events, state, simNow);
  const watchdog = watchdogFlag(events);
  return {
    case_id: caseId,
    pathway: "colorectal_fit",
    state,
    created_at: events[0]?.at ?? simNow,
    updated_at: events[events.length - 1]?.at ?? simNow,
    owner: currentOwner(events)?.name ?? null,
    due_at: clock.due_at,
    overdue: clock.due_at !== null && new Date(simNow).getTime() > new Date(clock.due_at).getTime(),
    escalation_level: escalationLevel(events),
    consent_frozen: consentFrozen(events),
    last_event_at: events[events.length - 1]?.at ?? simNow,
    watchdog_flagged: watchdog,
    risk_score: risk,
  };
}

/** Fill read-model fields a backend row lacks (older server, partial rows) so the UI never crashes. */
export function completeSummary(row: Partial<CaseSummary> & Pick<CaseSummary, "case_id">): CaseSummary {
  return {
    pathway: "colorectal_fit",
    state: "result_received",
    created_at: "",
    updated_at: "",
    ...row,
    owner: row.owner ?? null,
    due_at: row.due_at ?? null,
    overdue: row.overdue ?? false,
    escalation_level: row.escalation_level ?? 0,
    consent_frozen: row.consent_frozen ?? false,
    last_event_at: row.last_event_at ?? row.updated_at ?? "",
  };
}

/** Same for GET /cases/{id}: missing fields are derived from the event log. */
export function completeDetail(d: CaseDetail): CaseDetail {
  const events = d.events ?? [];
  return {
    ...d,
    events,
    owner: d.owner !== undefined ? d.owner : currentOwner(events)?.name ?? null,
    due_at: d.due_at ?? null,
    overdue: d.overdue ?? overdueMarker(events) !== null,
    escalation_level: d.escalation_level ?? escalationLevel(events),
    consent_frozen: d.consent_frozen ?? consentFrozen(events),
    last_event_at: d.last_event_at ?? events.at(-1)?.at ?? d.updated_at ?? "",
    watchdog_flagged: d.watchdog_flagged !== undefined ? d.watchdog_flagged : watchdogFlag(events),
  };
}

/** Events that annotate the log without moving the case (engine: accepted on any non-closed case). */
export const ANNOTATION_EVENTS = [
  "handoff_requested", "handoff_accepted", "handoff_rejected", "escalate", "escalation_accepted",
  "callback_requested", "consent_updated", "message_drafted", "message_approved", "message_sent",
  "mark_overdue", "watchdog_flagged", "mark_lost_contact",
];

/** Mirrors api/app/state_machine.apply for the mock. Throws ApiError(409) like the real API. */
export function validateAppend(state: string, type: string, actor: string, payload: Record<string, unknown>) {
  if (ANNOTATION_EVENTS.includes(type)) {
    if (state === "closed") throw new ApiError(409, "case is closed");
    return;
  }
  const t = transitionFor(state, type);
  if (!t) throw new ApiError(409, `event '${type}' not allowed from state '${state}'`);
  if (type === "reopen" && actorRole(actor) !== "clinician") {
    throw new ApiError(403, "only a clinician may reopen a closed case");
  }
  if (t.to === "closed" && isAutomatedActor(actor)) {
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
  /** Taxonomy code (capacity | wrong_service | missing_info | patient_declined) when rejected. */
  reason?: string;
  at: string;
}

/**
 * One entry per `handoff_requested`, resolved by the next `handoff_accepted|rejected`. The provider that
 * answered is taken from the payload (`provider`) or the actor (`provider:<name>`); with neither, the
 * answer applies to the oldest request still open.
 */
export function handoffs(events: CaseEvent[]): HandoffView[] {
  const out: HandoffView[] = [];
  for (const ev of events) {
    if (ev.type === "handoff_requested") {
      out.push({ provider: String(ev.payload.to_provider ?? ""), status: "requested", at: ev.at });
    } else if (ev.type === "handoff_accepted" || ev.type === "handoff_rejected") {
      const hint =
        typeof ev.payload.provider === "string" ? ev.payload.provider : actorRole(ev.actor) === "provider" ? actorName(ev.actor) : "";
      const open = out.find((h) => h.status === "requested" && (!hint || h.provider === hint));
      const rejected = ev.type === "handoff_rejected";
      const reason = typeof ev.payload.reason === "string" ? ev.payload.reason : undefined;
      if (open) {
        open.status = rejected ? "rejected" : "accepted";
        open.reason = reason;
        open.at = ev.at;
      } else out.push({ provider: hint || "Unknown provider", status: rejected ? "rejected" : "accepted", reason, at: ev.at });
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
  /** Set by the backend's `message_sent`, which can only follow an approval. */
  sent_at?: string;
}

/** AI-draft provenance: who/what produced each draft, who approved it, and whether it was sent. */
export function drafts(events: CaseEvent[]): DraftView[] {
  const out: DraftView[] = [];
  for (const ev of events) {
    if (ev.type === "message_drafted") {
      out.push({
        draft_id: String(ev.payload.draft_id),
        provider: String(ev.payload.provider ?? "unknown"),
        fallback_used: Boolean(ev.payload.fallback_used),
        text_ar: typeof ev.payload.text_ar === "string" ? ev.payload.text_ar : undefined,
        text_en: typeof ev.payload.text_en === "string" ? ev.payload.text_en : undefined,
        generated_at: ev.at,
        generated_by: ev.actor,
      });
    } else if (ev.type === "message_approved") {
      const d = out.find((x) => x.draft_id === String(ev.payload.draft_id));
      if (d) {
        d.approved_by = typeof ev.payload.approved_by === "string" ? ev.payload.approved_by : actorName(ev.actor) || ev.actor;
        d.approved_at = ev.at;
      }
    } else if (ev.type === "message_sent") {
      const d = out.find((x) => x.draft_id === String(ev.payload.draft_id));
      if (d && d.approved_at) d.sent_at = ev.at;
    }
  }
  return out;
}

export function rejectionReasons(events: CaseEvent[]): { provider: string; reason: string | undefined; at: string }[] {
  return handoffs(events)
    .filter((h) => h.status === "rejected")
    .map((h) => ({ provider: h.provider, reason: h.reason, at: h.at }));
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
