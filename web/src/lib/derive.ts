/** Derivations shared by the mock adapter and the UI (state, owner, SLA clock, handoffs, consent, drafts). */
import type {
  CaseDetail, CaseEvent, CaseSummary, Consent, ConsentChanges, ConsentHistoryEntry, DraftRecord, EscalationRung, Lang,
  LastHandoff, PendingHandoff, TimelineItem, WatchdogFlag,
} from "../api/contract";
import { ApiError, CONSENT_KEYS, HANDOFF_REJECT_REASONS } from "../api/contract";
import {
  REFERRAL_STATES, SLA_CLOCK_FROM_HANDOFF, SLA_WORKING_DAYS, addWorkingDays, nextTransition,
  rungLevel, transitionFor, wholeDaysBetween,
} from "./pathway";

export const INITIAL_STATE = "result_received";

// ---- Actors ("role:name", or a bare "watchdog") ------------------------------------------

const AUTOMATED_ROLES = new Set(["system", "model", "watchdog", "scheduler"]);

export function actorRole(actor: string): string {
  const i = actor.indexOf(":");
  return i < 0 ? actor : actor.slice(0, i);
}

/**
 * Display names the API resolved for actor strings (A14): the log holds `role:opaque_user_id`, the names live in the
 * API's `users` table and come back as the `actors` map of `GET /cases/{id}` and `GET /cases/{id}/timeline`. The http
 * adapter registers them here as it reads, so `actorName` shows "Dr. Layla Haddad" and not a 12-hex id.
 */
const knownActors = new Map<string, string>();
export function registerActors(map: Record<string, { display_name?: string } | null> | undefined) {
  if (!map) return;
  for (const [actor, who] of Object.entries(map)) if (who?.display_name) knownActors.set(actor, who.display_name);
}

/** The name part of `role:name` (the display name the API gave, when known); "" for a bare actor such as `watchdog`. */
export function actorName(actor: string): string {
  const known = knownActors.get(actor);
  if (known) return known;
  const i = actor.indexOf(":");
  return i < 0 ? "" : actor.slice(i + 1);
}

/** Automated actors (scheduler, watchdog, models, system jobs) get an "automated" chip in the UI. */
export function isAutomatedActor(actor: string): boolean {
  return AUTOMATED_ROLES.has(actorRole(actor));
}

/**
 * The state after `ev`, or null when `ev` leaves it alone. Main-path and side transitions come from the
 * pathway table; a rejected handoff (human, or the scheduler's bounce) also sends the case to
 * `referral_rejected`, but only from a referral state.
 */
export function nextState(state: string, ev: { type: string }): string | null {
  const t = transitionFor(state, ev.type);
  if (t) return t.to;
  if (ev.type === "handoff_rejected" && REFERRAL_STATES.includes(state)) return "referral_rejected";
  return null;
}

/** Replays events exactly like the engine: only transition events change state. */
export function replayState(events: CaseEvent[]): string {
  let state = INITIAL_STATE;
  for (const ev of events) state = nextState(state, ev) ?? state;
  return state;
}

/** Timestamp at which the case entered its current state. */
export function stateEnteredAt(events: CaseEvent[]): string {
  let state = INITIAL_STATE;
  let at = events[0]?.at ?? new Date(0).toISOString();
  for (const ev of events) {
    const to = nextState(state, ev);
    if (to) {
      state = to;
      at = ev.at;
    }
  }
  return at;
}

// ---- Owner, provider and handoffs (A5) --------------------------------------------------------

export interface OwnerView {
  /** `role:name` as the backend knows them. */
  actor: string;
  name: string;
  /** Display role ("Primary care physician"), from the payload or the actor's backend role. */
  role: string;
}

const ROLE_LABEL: Record<string, string> = {
  primary_care_owner: "Primary care physician",
  specialist_owner: "Specialist",
  supervisor: "Supervisor",
  coordinator: "Care coordinator",
};

function ownerFrom(ev: CaseEvent): OwnerView {
  const role = typeof ev.payload.owner_role === "string" ? ev.payload.owner_role : ROLE_LABEL[actorRole(ev.actor)] ?? actorRole(ev.actor);
  return { actor: ev.actor, name: actorName(ev.actor) || ev.actor, role };
}

export interface HandoffReplay {
  owner: OwnerView | null;
  pending: PendingHandoff | null;
  last: LastHandoff | null;
  /** The provider the current owner acts for: the provider of the latest accepted handoff. */
  provider: string | null;
}

/**
 * Owner and handoff state by replay (A5, ADR-016). The owner changes only on accept_ownership,
 * handoff_accepted and reassign, never on a mere request. `pending` is set by handoff_requested and cleared
 * by any answer or a reassignment; `last` is the latest answer, with `bounced` for the scheduler's timeout.
 */
export function replayHandoffs(events: CaseEvent[]): HandoffReplay {
  let owner: OwnerView | null = null;
  let pending: PendingHandoff | null = null;
  let last: LastHandoff | null = null;
  let provider: string | null = null;
  for (const ev of events) {
    if (ev.type === "accept_ownership") {
      if (!isAutomatedActor(ev.actor)) owner = ownerFrom(ev);
    } else if (ev.type === "reassign" && typeof ev.payload.owner === "string") {
      const actor = ev.payload.owner.includes(":") ? ev.payload.owner : `primary_care_owner:${ev.payload.owner}`;
      owner = { actor, name: actorName(actor), role: ROLE_LABEL[actorRole(actor)] ?? "Primary care physician" };
      pending = null;
    } else if (ev.type === "handoff_requested") {
      pending = { to_provider: String(ev.payload.to_provider ?? ""), requested_by: ev.actor, from_owner: owner?.actor ?? null, requested_at: ev.at };
    } else if (ev.type === "handoff_accepted") {
      const to = typeof ev.payload.provider === "string" ? ev.payload.provider : pending?.to_provider ?? "";
      last = { outcome: "accepted", to_provider: to, by: ev.actor, at: ev.at };
      provider = to || provider;
      if (!isAutomatedActor(ev.actor)) owner = ownerFrom(ev);
      pending = null;
    } else if (ev.type === "handoff_rejected") {
      last = {
        outcome: "rejected", to_provider: pending?.to_provider ?? (typeof ev.payload.provider === "string" ? ev.payload.provider : ""),
        by: ev.actor, at: ev.at,
        ...(typeof ev.payload.reason === "string" ? { reason: ev.payload.reason } : {}),
        ...(ev.payload.bounced === true ? { bounced: true } : {}),
      };
      pending = null;
    }
  }
  return { owner, pending, last, provider };
}

export const ownerOf = (events: CaseEvent[]): OwnerView | null => replayHandoffs(events).owner;
export const pendingHandoffOf = (events: CaseEvent[]): PendingHandoff | null => replayHandoffs(events).pending;
export const lastHandoffOf = (events: CaseEvent[]): LastHandoff | null => replayHandoffs(events).last;
export const providerOf = (events: CaseEvent[]): string | null => replayHandoffs(events).provider;

/** Back-compat for the patient page: the owner's display name and role. */
export function currentOwner(events: CaseEvent[]): { name: string; role?: string; actor?: string } | null {
  const o = ownerOf(events);
  return o ? { name: o.name, role: o.role, actor: o.actor } : null;
}

/** Latest `handoff_accepted` (referral acceptance), the start of the confirmatory SLA clock (A13). */
function latestReferralAcceptance(events: CaseEvent[]): string | null {
  for (let i = events.length - 1; i >= 0; i--) if (events[i].type === "handoff_accepted") return events[i].at;
  return null;
}

export function slaClock(events: CaseEvent[], state: string, simNow: string) {
  const next = nextTransition(state);
  const days = next ? SLA_WORKING_DAYS[next.event] : undefined;
  if (!next || days === undefined) return { due_at: null as string | null, started_at: null as string | null, days_overdue: 0, sla_event: next?.event ?? null };
  const started = (SLA_CLOCK_FROM_HANDOFF.includes(next.event) ? latestReferralAcceptance(events) : null) ?? stateEnteredAt(events);
  const due = addWorkingDays(started, days);
  return { due_at: due, started_at: started, days_overdue: Math.max(0, wholeDaysBetween(due, simNow)), sla_event: next.event };
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
    const to = nextState(state, ev);
    if (to) {
      state = to;
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
      pending = { rung: String(ev.payload.rung ?? ""), target_role: String(ev.payload.target ?? ev.payload.target_role ?? ""), at: ev.at };
    } else if (ev.type === "escalation_accepted") pending = null;
  }
  return pending;
}

/** Programme defaults (A6): everything is allowed until the patient says otherwise. */
export const DEFAULT_CONSENT: Consent = {
  channel_sms: true, channel_whatsapp: true, channel_phone: true, channel_letter: true,
  share_with_specialist: true, withdrawn: false, updated_at: null, updated_by: null,
};

/** The booleans of a `consent_updated` payload (anything else is ignored; the engine answers 422 to it). */
export function consentChangesOf(payload: Record<string, unknown>): ConsentChanges {
  const out: ConsentChanges = {};
  for (const k of CONSENT_KEYS) if (typeof payload[k] === "boolean") out[k] = payload[k] as boolean;
  return out;
}

/** Consent by replaying every `consent_updated` in order over the defaults (A6). */
export function consentOf(events: CaseEvent[]): Consent {
  let c: Consent = { ...DEFAULT_CONSENT };
  for (const ev of events) {
    if (ev.type === "consent_updated") c = { ...c, ...consentChangesOf(ev.payload), updated_at: ev.at, updated_by: ev.actor };
  }
  return c;
}

export function consentHistoryOf(events: CaseEvent[]): ConsentHistoryEntry[] {
  return events.filter((e) => e.type === "consent_updated").map((e) => ({ at: e.at, actor: e.actor, changes: consentChangesOf(e.payload) }));
}

/** Outreach is frozen exactly while consent is withdrawn. The real read model's `consent_frozen` wins over this. */
export function consentFrozen(events: CaseEvent[]): boolean {
  return consentOf(events).withdrawn;
}

/** Channels the patient currently allows, in display order. */
export function allowedChannels(c: Consent): ("sms" | "whatsapp" | "phone" | "letter")[] {
  return (["sms", "whatsapp", "phone", "letter"] as const).filter((ch) => c[`channel_${ch}`]);
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
  const h = replayHandoffs(events);
  return {
    case_id: caseId,
    pathway: "colorectal_fit",
    state,
    created_at: events[0]?.at ?? simNow,
    updated_at: events[events.length - 1]?.at ?? simNow,
    owner: h.owner?.name ?? null,
    owner_actor: h.owner?.actor ?? null,
    owner_role: h.owner?.role ?? null,
    provider: h.provider,
    pending_handoff: h.pending,
    last_handoff: h.last,
    due_at: clock.due_at,
    overdue: clock.due_at !== null && new Date(simNow).getTime() > new Date(clock.due_at).getTime(),
    escalation_level: escalationLevel(events),
    consent_frozen: consentFrozen(events),
    last_event_at: events[events.length - 1]?.at ?? simNow,
    watchdog_flagged: watchdog,
    risk_score: risk,
  };
}

/**
 * The owner on the wire is `{actor, display_name, role}` (A14) or, until then, the actor string
 * (`primary_care_owner:Dr Sara Khan`) or a bare name. Normalised to a display name plus the rest.
 */
export function normalizeOwner(raw: unknown): { owner: string | null; owner_actor: string | null; owner_role: string | null } {
  if (raw && typeof raw === "object") {
    const o = raw as { actor?: unknown; display_name?: unknown; role?: unknown };
    const actor = typeof o.actor === "string" ? o.actor : null;
    const name = typeof o.display_name === "string" && o.display_name ? o.display_name : actor ? actorName(actor) || actor : null;
    return { owner: name, owner_actor: actor, owner_role: typeof o.role === "string" ? o.role : null };
  }
  if (typeof raw === "string" && raw) {
    return raw.includes(":")
      ? { owner: actorName(raw) || raw, owner_actor: raw, owner_role: null }
      : { owner: raw, owner_actor: null, owner_role: null };
  }
  return { owner: null, owner_actor: null, owner_role: null };
}

/** Fill read-model fields a backend row lacks (older server, partial rows) so the UI never crashes. */
export function completeSummary(row: Partial<CaseSummary> & Pick<CaseSummary, "case_id">): CaseSummary {
  const o = normalizeOwner((row as { owner?: unknown }).owner);
  return {
    pathway: "colorectal_fit",
    state: "result_received",
    created_at: "",
    updated_at: "",
    ...row,
    owner: o.owner,
    owner_actor: row.owner_actor ?? o.owner_actor,
    owner_role: row.owner_role ?? o.owner_role,
    due_at: row.due_at ?? (row as { breach_due_at?: string | null }).breach_due_at ?? null,
    overdue: row.overdue ?? false,
    escalation_level: row.escalation_level ?? 0,
    consent_frozen: row.consent_frozen ?? false,
    last_event_at: row.last_event_at ?? row.updated_at ?? "",
    watchdog_flagged: row.watchdog_flagged ?? null,
    provider: row.provider ?? null,
    pending_handoff: row.pending_handoff ?? null,
    last_handoff: row.last_handoff ?? null,
  };
}

/**
 * Same for GET /cases/{id}. The real response nests the read model under `read_model` (and `GET /cases`
 * flattens it in A9); both are accepted. Missing fields are derived from the event log.
 */
export function completeDetail(raw: CaseDetail): CaseDetail {
  const rm = (raw as { read_model?: Record<string, unknown> }).read_model ?? {};
  const d = { ...rm, ...raw } as CaseDetail & Record<string, unknown>;
  const events = d.events ?? [];
  const h = replayHandoffs(events);
  const o = d.owner !== undefined ? normalizeOwner(d.owner) : normalizeOwner(h.owner ? { actor: h.owner.actor, display_name: h.owner.name, role: h.owner.role } : null);
  const consent = (d.consent as Consent | undefined) ? { ...DEFAULT_CONSENT, ...(d.consent as Consent) } : consentOf(events);
  const frozen = d.consent_frozen ?? consent.withdrawn;
  return {
    ...d,
    events,
    owner: o.owner,
    owner_actor: d.owner_actor ?? o.owner_actor,
    owner_role: d.owner_role ?? o.owner_role,
    due_at: d.due_at ?? (d as { breach_due_at?: string | null }).breach_due_at ?? null,
    overdue: d.overdue ?? overdueMarker(events) !== null,
    escalation_level: d.escalation_level ?? escalationLevel(events),
    consent_frozen: frozen,
    consent,
    consent_history: d.consent_history ?? consentHistoryOf(events),
    review_reason: d.review_reason !== undefined ? d.review_reason : frozen ? "consent_withdrawn" : null,
    last_event_at: d.last_event_at ?? events.at(-1)?.at ?? d.updated_at ?? "",
    watchdog_flagged: d.watchdog_flagged !== undefined ? d.watchdog_flagged : watchdogFlag(events),
    provider: d.provider !== undefined ? d.provider : h.provider,
    pending_handoff: d.pending_handoff !== undefined ? d.pending_handoff : h.pending,
    last_handoff: d.last_handoff !== undefined ? d.last_handoff : h.last,
  };
}

/** Events that annotate the log without moving the case (engine: accepted on any non-closed case). */
export const ANNOTATION_EVENTS = [
  "handoff_requested", "handoff_accepted", "handoff_rejected", "escalate", "escalation_accepted",
  "callback_requested", "consent_updated", "message_drafted", "message_approved", "message_sent",
  "mark_overdue", "watchdog_flagged", "mark_lost_contact", "review_requested",
];

/** Backend roles that are owner-scoped (A5): they act only on cases they own. */
const OWNER_BACKEND_ROLES = new Set(["primary_care_owner", "specialist_owner"]);
/** Roles that count as the clinician for reopen (the UI's own "clinician" prefix is still accepted by the mock). */
const CLINICIAN_ACTOR_ROLES = new Set(["clinician", "primary_care_owner", "specialist_owner"]);
/** Events that freeze while consent is withdrawn (A6). */
const FROZEN_EVENTS = ["contact_patient", "message_drafted", "message_approved", "message_sent", "handoff_requested"];

export interface AppendContext {
  /** An in-process write by the mock itself (the simulated scheduler, notifier, API): skips role and ownership checks. */
  internal?: boolean;
  /** The provider the acting clinician acts for (their login claim). */
  provider?: string | null;
}

/** True when the backend would refuse a case action from this actor because they are an owner-scoped clinician who does not own it. */
export function isOwnerScopedActor(actor: string): boolean {
  return OWNER_BACKEND_ROLES.has(actorRole(actor));
}

/**
 * Mirrors api/app/state_machine.apply and api/app/policy.py for the mock. Throws ApiError with the status
 * the real API uses: 403 role or owner scope, 409 invalid transition or frozen outreach, 422 malformed payload.
 */
export function validateAppend(events: CaseEvent[], type: string, actor: string, payload: Record<string, unknown>, ctx: AppendContext = {}) {
  const state = replayState(events);
  const h = replayHandoffs(events);
  const frozen = consentOf(events).withdrawn;

  if (!ctx.internal) {
    if (["message_approved", "message_sent", "review_requested"].includes(type)) {
      throw new ApiError(403, `nobody may post '${type}' through the events route`);
    }
    if (isOwnerScopedActor(actor)) {
      const mine = h.owner?.actor === actor;
      const takesUnowned = type === "accept_ownership" && h.owner === null;
      const answers = (type === "handoff_accepted" || type === "handoff_rejected") && h.pending !== null && !!ctx.provider && h.pending.to_provider === ctx.provider;
      if (!mine && !takesUnowned && !answers) {
        throw new ApiError(403, `${actorRole(actor)} '${actorName(actor)}' may only act on cases they own (this case's owner is ${h.owner?.name ?? "not yet assigned"})`);
      }
    }
  }
  if (state === "closed" && type !== "reopen") throw new ApiError(409, "case is closed");
  if (frozen && FROZEN_EVENTS.includes(type) && !(ctx.internal && type === "message_sent")) {
    throw new ApiError(409, `consent withdrawn: '${type}' is refused while the patient's consent is withdrawn; a human must review the case`);
  }

  if (type === "handoff_requested") {
    const to = payload.to_provider;
    if (typeof to !== "string" || !to.trim()) throw new ApiError(422, "handoff_requested needs a non-empty string 'to_provider'");
    if (!h.owner) throw new ApiError(409, "handoff_requested: the case has no owner to hand over from");
    if (h.pending) throw new ApiError(409, `a handoff to '${h.pending.to_provider}' is already pending; it must be answered or bounce first`);
  } else if (type === "handoff_accepted" || type === "handoff_rejected") {
    if (!h.pending) throw new ApiError(409, `${type}: there is no pending handoff request`);
    if (!ctx.internal && (actor === h.pending.requested_by || actor === h.pending.from_owner) && type === "handoff_accepted") {
      throw new ApiError(409, "the requester (or the owner the case comes from) cannot accept their own handoff");
    }
    if (type === "handoff_rejected") {
      const reason = payload.reason;
      const bounce = reason === "timeout";
      if (bounce ? actor !== "system:scheduler" || payload.bounced !== true : !(HANDOFF_REJECT_REASONS as readonly string[]).includes(String(reason))) {
        throw new ApiError(422, `handoff_rejected reason must be one of ${JSON.stringify(HANDOFF_REJECT_REASONS)}, got ${JSON.stringify(reason)}`);
      }
    }
  } else if (type === "consent_updated") {
    const given = Object.keys(payload).filter((k) => (CONSENT_KEYS as readonly string[]).includes(k));
    if (given.length === 0) throw new ApiError(422, `consent_updated needs at least one of ${JSON.stringify(CONSENT_KEYS)}`);
    if (given.some((k) => typeof payload[k] !== "boolean")) throw new ApiError(422, "consent_updated values must be booleans");
  }

  if (ANNOTATION_EVENTS.includes(type) && !(type === "handoff_accepted" && state === "referral_rejected")) return;
  const t = transitionFor(state, type);
  if (!t) throw new ApiError(409, `event '${type}' not allowed from state '${state}'`);
  if (type === "reopen" && !CLINICIAN_ACTOR_ROLES.has(actorRole(actor))) {
    throw new ApiError(403, "only a clinician may reopen a closed case");
  }
  if (type === "reassign" && !["coordinator", "supervisor"].includes(actorRole(actor)) && !ctx.internal) {
    throw new ApiError(403, "only a coordinator or supervisor may reassign a rejected referral");
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
  /** Taxonomy code (capacity | wrong_service | missing_info | patient_declined, or timeout) when rejected. */
  reason?: string;
  /** True when the scheduler bounced an unanswered request (reason "timeout"). */
  bounced?: boolean;
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
        open.bounced = ev.payload.bounced === true || undefined;
        open.at = ev.at;
      } else out.push({ provider: hint || "Unknown provider", status: rejected ? "rejected" : "accepted", reason, bounced: ev.payload.bounced === true || undefined, at: ev.at });
    }
  }
  return out;
}

/** Draft status for the review panel. */
export type DraftStatus = "pending" | "approved" | "rejected";

export interface DraftView extends DraftRecord {
  status: DraftStatus;
  /** Set when a `message_sent {draft_id}` exists for an approved draft (it can only follow an approval). */
  sent_at?: string;
}

/**
 * Drafts for the review panel. `GET /cases/{id}/drafts` is the source (the timeline events carry no text);
 * drafts the API did not return (an older backend, a role that may not list them) are rebuilt from the
 * `message_drafted` events, including any text an older payload still carries.
 */
export function draftViews(records: DraftRecord[], events: CaseEvent[]): DraftView[] {
  const byId = new Map(records.map((r) => [r.draft_id, r]));
  for (const ev of events) {
    if (ev.type !== "message_drafted") continue;
    const id = String(ev.payload.draft_id);
    if (byId.has(id)) continue;
    const lang: Lang = ev.payload.language === "ar" || typeof ev.payload.text_ar === "string" ? "ar" : "en";
    const text_ar = typeof ev.payload.text_ar === "string" ? ev.payload.text_ar : null;
    const text_en = typeof ev.payload.text_en === "string" ? ev.payload.text_en : null;
    byId.set(id, {
      draft_id: id, case_id: "", purpose: "", channel: "", language: lang,
      text: (lang === "ar" ? text_ar : text_en) ?? text_en ?? text_ar ?? "",
      text_ar, text_en,
      provider: String(ev.payload.provider ?? "unknown"), fallback_used: Boolean(ev.payload.fallback_used),
      abstention_reason: typeof ev.payload.abstention_reason === "string" ? ev.payload.abstention_reason : null,
      prompt_hash: typeof ev.payload.prompt_hash === "string" ? ev.payload.prompt_hash : "",
      created_by: ev.actor, created_at: ev.at, approved_at: null, approved_by: null,
      rejected_at: null, rejected_by: null, rejection_reason: null,
    });
  }
  const sent = new Map<string, string>();
  for (const ev of events) {
    if (ev.type === "message_sent" && typeof ev.payload.draft_id === "string") sent.set(ev.payload.draft_id, ev.at);
    if (ev.type === "message_approved" && typeof ev.payload.draft_id === "string") {
      const r = byId.get(ev.payload.draft_id);
      if (r && !r.approved_at) byId.set(r.draft_id, { ...r, approved_at: ev.at, approved_by: ev.actor });
    }
  }
  return [...byId.values()]
    .sort((a, b) => a.created_at.localeCompare(b.created_at))
    .map((r) => ({
      ...r,
      status: r.rejected_at ? "rejected" : r.approved_at ? "approved" : "pending",
      ...(r.approved_at && sent.has(r.draft_id) ? { sent_at: sent.get(r.draft_id) } : {}),
    }));
}

export function rejectionReasons(events: CaseEvent[]): { provider: string; reason: string | undefined; at: string; bounced: boolean }[] {
  return handoffs(events)
    .filter((h) => h.status === "rejected")
    .map((h) => ({ provider: h.provider, reason: h.reason, at: h.at, bounced: Boolean(h.bounced) }));
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
