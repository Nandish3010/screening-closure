/** Derivations shared by the mock adapter and the UI (state, owner, SLA clock, handoffs, consent, drafts). */
import type {
  ActorKind, CaseDetail, CaseEvent, ClosureProposal, IncompleteProcedure, CaseSummary, Consent, ConsentChanges, ConsentHistoryEntry, DraftRecord, EscalationRung, Lang,
  InformedRefusal, LastHandoff, MilestoneClock, MilestoneName, PendingHandoff, TimelineItem, WatchdogFlag,
} from "../api/contract";
import {
  APPOINTMENT_CANCEL_REASONS, ApiError, AUTOMATION_ACTOR, CONSENT_KEYS, REFUSAL_REASONS, HANDOFF_REJECT_REASONS, OUTREACH_HOLD_REASONS, REVIEW_REASONS, TRIAGE_REVIEW_REASONS } from "../api/contract";
import {
  MILESTONES, RE_OFFER_MONTHS, REFERRAL_STATES, SLA_WORKING_DAYS, addWorkingDays, nextTransition,
  rungLevel, transitionFor, wholeDaysBetween,
} from "./pathway";

export const INITIAL_STATE = "result_received";

// ---- Actors ("role:name", or a bare "watchdog") ------------------------------------------

const AUTOMATED_ROLES = new Set(["system", "model", "watchdog", "scheduler"]);

export function actorRole(actor: string): string {
  const i = actor.indexOf(":");
  return i < 0 ? actor : actor.slice(0, i);
}

const SERVICE_ACTORS = new Set(["scheduler", "watchdog"]);

/**
 * Who wrote an event (A21), derived from the actor string with the API's rule: `system:automation` is automation, `model:*`
 * a model, any other `system:*` (scheduler, watchdog, notifier, consent, intake) and a bare `scheduler` / `watchdog` a
 * service, and every user role (lab, primary_care_owner, specialist_owner, coordinator, supervisor, patient) a human.
 * The wire's own `actor_kind` wins where there is one (`kindOfEvent`).
 */
export function actorKindOf(actor: string): ActorKind {
  if (actor === AUTOMATION_ACTOR) return "automation";
  const role = actorRole(actor);
  if (role === "model") return "model";
  if (role === "system" || SERVICE_ACTORS.has(role)) return "service";
  return "human";
}

/** The kind of an event's actor: the API's `actor_kind` when present, else derived from the actor string. */
export function kindOfEvent(ev: { actor: string; actor_kind?: ActorKind }): ActorKind {
  return ev.actor_kind ?? actorKindOf(ev.actor);
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

/** The user id inside an actor (`role:user_id`) or a Practitioner reference (`Practitioner/user_id`); the machine compares people by it (A33). */
export function personId(identity: string): string {
  return identity.startsWith("Practitioner/") ? identity.slice("Practitioner/".length) : identity.slice(identity.indexOf(":") + 1);
}

/** Calendar months added to an ISO timestamp, the day clamped to the end of a shorter month (mirrors state_machine.add_months). */
export function addMonths(iso: string, months: number): string {
  const d = new Date(iso);
  const day = d.getUTCDate();
  d.setUTCDate(1);
  d.setUTCMonth(d.getUTCMonth() + months);
  const last = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate();
  d.setUTCDate(Math.min(day, last));
  return d.toISOString();
}

/**
 * The informed refusal on record by replay (A33): the latest `record_informed_refusal`, cleared by a `reopen`. The re-offer date
 * is the refusal's date plus RE_OFFER_MONTHS (the YAML's `informed_refusal.re_offer_months`); a date only, nothing re-offers.
 */
export function refusalOf(events: CaseEvent[]): InformedRefusal | null {
  let out: InformedRefusal | null = null;
  for (const ev of events) {
    if (ev.type === "record_informed_refusal") {
      out = {
        counselling_ref: String(ev.payload.counselling_ref ?? ""), counselled_by: String(ev.payload.counselled_by ?? ""),
        reason: ev.payload.reason as InformedRefusal["reason"], recorded_at: ev.at, event_id: ev.event_id ?? `seq-${ev.seq}`,
        re_offer_due_at: addMonths(ev.at, RE_OFFER_MONTHS),
      };
    } else if (ev.type === "reopen") out = null;
  }
  return out;
}

/** How the case closed (A33): `declined` for an informed-refusal closure, else `investigated`; null while open. */
export function closureOutcomeOf(events: CaseEvent[]): "declined" | "investigated" | null {
  let out: "declined" | "investigated" | null = null;
  for (const ev of events) {
    if (ev.type === "close") out = ev.payload.outcome === "declined" ? "declined" : "investigated";
    else if (ev.type === "reopen") out = null;
  }
  return out;
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
    } else if (ev.type === "assign_ownership" && typeof ev.payload.owner === "string") {
      // A21: the automation actor sets the owner WITHOUT acceptance (the state stays `acknowledged`).
      const actor = ev.payload.owner;
      owner = { actor, name: actorName(actor), role: ROLE_LABEL[actorRole(actor)] ?? actorRole(actor) };
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

/**
 * The owner as a patient's page needs it (SEC-2). A staff-shaped answer names a person (`owner_actor` is `role:id`); the
 * redacted answer a patient gets carries the role only (`owner_actor` is the bare role, no name, no id). Returns null when
 * nobody owns the case. `roleKey` is the backend role (`primary_care_owner`...) when it is known.
 */
export function patientOwnerOf(d: { owner?: string | null; owner_actor?: string | null; owner_role?: string | null; events: CaseEvent[] }):
  { kind: "role"; roleKey: string } | { kind: "person"; name: string; role?: string } | null {
  const actor = d.owner_actor ?? null;
  if (actor !== null && !actor.includes(":")) return { kind: "role", roleKey: actor.trim() }; // redacted: the actor is the role
  const person = currentOwner(d.events);
  const name = person?.name ?? d.owner ?? null;
  if (!name) return null;
  return { kind: "person", name, role: person?.role ?? d.owner_role ?? undefined };
}

/** Back-compat for the patient page: the owner's display name and role. */
export function currentOwner(events: CaseEvent[]): { name: string; role?: string; actor?: string } | null {
  const o = ownerOf(events);
  return o ? { name: o.name, role: o.role, actor: o.actor } : null;
}

export function slaClock(events: CaseEvent[], state: string, simNow: string) {
  const next = nextTransition(state);
  const days = next ? SLA_WORKING_DAYS[next.event] : undefined;
  if (!next || days === undefined) return { due_at: null as string | null, started_at: null as string | null, days_overdue: 0, sla_event: next?.event ?? null };
  const started = stateEnteredAt(events);
  const due = addWorkingDays(started, days);
  return { due_at: due, started_at: started, days_overdue: Math.max(0, wholeDaysBetween(due, simNow)), sla_event: next.event };
}

/**
 * Milestone clocks by replay (A18, ADR-028), the mock's and the UI's fallback for a server that sends no
 * `milestones`. A milestone starts at its `from_event` (the FIRST occurrence, and it never restarts, A25; `case_created`
 * = the first event), or, if that never happened, when the case enters the state that awaits its `until_event`; it is
 * met by the `until_event` and void if the case closes first. `overdue` here is "past due at `simNow`", where the
 * real read model has the scheduler's flag.
 */
export function milestonesOf(events: CaseEvent[], simNow: string | null = null): Record<MilestoneName, MilestoneClock> {
  const out = {} as Record<MilestoneName, MilestoneClock>;
  for (const def of MILESTONES) {
    let started: string | null = def.from_event === "case_created" ? events[0]?.at ?? null : null;
    let met: string | null = null;
    let isVoid = false;
    let state = INITIAL_STATE;
    for (const ev of events) {
      const to = nextState(state, ev);
      if (met === null && !isVoid) {
        if (ev.type === def.until_event) met = ev.at;
        else if (ev.type === def.from_event && def.from_event !== "case_created" && started === null) started = ev.at; // first only (A25)
        else if (started === null && to && nextTransition(to)?.event === def.until_event) started = ev.at;
        if (to === "closed" && met === null) isVoid = true;
      }
      if (to) state = to;
    }
    const due = started && !isVoid ? addWorkingDays(started, def.working_days) : null;
    out[def.name] = {
      due_at: due, met_at: met, started_at: started, working_days: def.working_days, void: isVoid,
      overdue: met === null && !isVoid && due !== null && simNow !== null && new Date(simNow).getTime() > new Date(due).getTime(),
      met_late: met !== null && due !== null ? new Date(met).getTime() > new Date(due).getTime() : null,
    };
  }
  return out;
}

/** The next deadline on a case: the earlier of the state's SLA expiry and every running milestone's due date. */
export function nextDeadline(stateDue: string | null, milestones: Partial<Record<MilestoneName, MilestoneClock>>): string | null {
  const dues = [stateDue, ...Object.values(milestones).filter((m) => m && m.met_at === null && !m.void).map((m) => m!.due_at)]
    .filter((d): d is string => !!d);
  return dues.length ? dues.reduce((a, b) => (new Date(a).getTime() <= new Date(b).getTime() ? a : b)) : null;
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
  const due = nextDeadline(clock.due_at, milestonesOf(events, simNow));
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
    due_at: due,
    overdue: due !== null && new Date(simNow).getTime() > new Date(due).getTime(),
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

/**
 * The open closure proposal (A21): the latest `closure_proposed`, unless a `close` or `reopen` came after it. The real read
 * model reports `closure_proposal` itself; this is the mock's answer and the fallback for a server that sends none.
 */
export function closureProposalOf(events: CaseEvent[]): ClosureProposal | null {
  let proposal: ClosureProposal | null = null;
  events.forEach((ev, i) => {
    if (ev.type === "closure_proposed" && typeof ev.payload.evidence_ref === "string") {
      proposal = { evidence_ref: ev.payload.evidence_ref, proposed_at: ev.at, event_id: ev.event_id ?? `seq-${ev.seq ?? i + 1}` };
    } else if (ev.type === "close" || ev.type === "reopen") proposal = null;
  });
  return proposal;
}

/**
 * The open `incomplete_procedure` flag (A24), by replay: set by a `review_requested` with that reason, cleared by a
 * `closure_proposed` (complete evidence was found), a `close` or a `reopen`. Mirrors the API's `read_model.incomplete_procedure`.
 */
export function incompleteProcedureOf(events: CaseEvent[]): IncompleteProcedure | null {
  let flag: IncompleteProcedure | null = null;
  for (const ev of events) {
    if (ev.type === "review_requested" && ev.payload.reason === "incomplete_procedure" && typeof ev.payload.evidence_ref === "string") {
      flag = { evidence_ref: ev.payload.evidence_ref, flagged_at: ev.at };
    } else if (ev.type === "closure_proposed" || ev.type === "close" || ev.type === "reopen") flag = null;
  }
  return flag;
}

/** The actor of the reply triage (D13): it writes `reply_received`, `reply_classified` and the triage review reasons. */
export const TRIAGE_ACTOR = "system:triage";

/** Who may give each `review_requested.reason` (the API's REVIEW_EMITTER): exactly one actor per reason. */
export const REVIEW_EMITTER: Record<string, string> = {
  consent_withdrawn: "system:consent",
  reroute_exhausted: AUTOMATION_ACTOR,
  incomplete_procedure: AUTOMATION_ACTOR,
  ...Object.fromEntries(TRIAGE_REVIEW_REASONS.map((r) => [r, TRIAGE_ACTOR])),
};

export interface TriageReplay {
  /** Open outreach holds (`opt_out`, `wrong_number`) in the order they were raised. */
  holds: string[];
  /** The latest triage flag still open (`triage_review_reason`), with when it was raised. */
  reason: string | null;
  at: string | null;
  /** `at` of the latest `contact_updated`: a person confirmed the contact details. */
  contactConfirmedAt: string | null;
}

/**
 * Replay of the triage flag and the outreach holds (D13), mirroring the API's read model. A `review_requested` from
 * `system:triage` sets the triage flag (and opens a hold for `opt_out` / `wrong_number`). A person clears it: a
 * `consent_updated` clears an `opt_out` hold, a `contact_updated` a `wrong_number` hold. The triage flag goes with its hold,
 * unless the person's answer was to withdraw consent (then the case is in consent review and keeps the triage reason).
 */
export function triageReplayOf(events: CaseEvent[]): TriageReplay {
  let holds: string[] = [];
  let reason: string | null = null;
  let at: string | null = null;
  let contactConfirmedAt: string | null = null;
  let withdrawn = false;
  const clear = (hold: string) => {
    if (!holds.includes(hold)) return;
    holds = holds.filter((h) => h !== hold);
    if (reason === hold && !withdrawn) { reason = null; at = null; }
  };
  for (const ev of events) {
    if (ev.type === "consent_updated") {
      const w = consentChangesOf(ev.payload).withdrawn;
      if (w !== undefined) withdrawn = w;
      clear("opt_out");
    } else if (ev.type === "contact_updated") {
      contactConfirmedAt = ev.at;
      clear("wrong_number");
    } else if (ev.type === "review_requested" && ev.actor === TRIAGE_ACTOR && typeof ev.payload.reason === "string") {
      reason = ev.payload.reason;
      at = ev.at;
      if ((OUTREACH_HOLD_REASONS as readonly string[]).includes(reason) && !holds.includes(reason)) holds = [...holds, reason];
    }
  }
  return { holds, reason, at, contactConfirmedAt };
}

/** The open outreach holds of a case: while one is open the automation sends no automatic message. */
export function outreachHoldsOf(events: CaseEvent[]): string[] {
  return triageReplayOf(events).holds;
}

/**
 * Why the case is on the human-review list, and since when, by replay. Precedence as the API: consent withdrawn while it is,
 * else the open triage reason (D13), else `reroute_exhausted` while the case still waits in `referral_rejected` after the
 * re-route list ran out (A21). Null when no human review is pending.
 */
export function reviewOf(events: CaseEvent[]): { reason: string | null; at: string | null } {
  if (consentOf(events).withdrawn) {
    const flag = [...events].reverse().find((e) => e.type === "review_requested" && e.payload.reason === "consent_withdrawn");
    return { reason: "consent_withdrawn", at: flag?.at ?? consentOf(events).updated_at ?? null };
  }
  const triage = triageReplayOf(events);
  if (triage.reason) return { reason: triage.reason, at: triage.at };
  const state = replayState(events);
  const lastExhausted = events.map((e) => e.type === "review_requested" && e.payload.reason === "reroute_exhausted").lastIndexOf(true);
  if (state === "referral_rejected" && lastExhausted >= 0) return { reason: "reroute_exhausted", at: events[lastExhausted].at };
  const incomplete = incompleteProcedureOf(events);
  return incomplete ? { reason: "incomplete_procedure", at: incomplete.flagged_at } : { reason: null, at: null };
}

export function reviewReasonOf(events: CaseEvent[]): string | null {
  return reviewOf(events).reason;
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
export function completeDetail(raw: CaseDetail, simNow: string | null = null): CaseDetail {
  const rm = (raw as { read_model?: Record<string, unknown> }).read_model ?? {};
  const d = { ...rm, ...raw } as CaseDetail & Record<string, unknown>;
  const events = d.events ?? [];
  const h = replayHandoffs(events);
  const o = d.owner !== undefined ? normalizeOwner(d.owner) : normalizeOwner(h.owner ? { actor: h.owner.actor, display_name: h.owner.name, role: h.owner.role } : null);
  const consent = (d.consent as Consent | undefined) ? { ...DEFAULT_CONSENT, ...(d.consent as Consent) } : consentOf(events);
  const frozen = d.consent_frozen ?? consent.withdrawn;
  const triage = triageReplayOf(events);
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
    review_reason: d.review_reason !== undefined ? d.review_reason : frozen ? "consent_withdrawn" : reviewReasonOf(events),
    review_requested_at: d.review_requested_at !== undefined ? d.review_requested_at : reviewOf(events).at,
    triage_review_reason: d.triage_review_reason !== undefined ? d.triage_review_reason : triage.reason,
    triage_review_requested_at: d.triage_review_requested_at !== undefined ? d.triage_review_requested_at : triage.at,
    outreach_holds: Array.isArray(d.outreach_holds) ? d.outreach_holds : triage.holds,
    closure_proposal: d.closure_proposal !== undefined ? d.closure_proposal : closureProposalOf(events),
    incomplete_procedure: d.incomplete_procedure !== undefined ? d.incomplete_procedure : incompleteProcedureOf(events),
    refusal: d.refusal !== undefined ? d.refusal : refusalOf(events),
    re_offer_due_at: d.re_offer_due_at !== undefined ? d.re_offer_due_at : refusalOf(events)?.re_offer_due_at ?? null,
    closure_outcome: d.closure_outcome !== undefined ? d.closure_outcome : closureOutcomeOf(events),
    milestones: d.milestones ?? milestonesOf(events, simNow),
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
  "callback_requested", "consent_updated", "message_drafted", "message_approved", "message_rejected", "message_sent",
  "mark_overdue", "watchdog_flagged", "mark_lost_contact", "review_requested", "assign_ownership", "closure_proposed",
  "contact_updated", "reply_received", "reply_classified",
];
/** Events only the automation actor (or another service) writes: nobody may post them through the events route. */
const SERVICE_ONLY_EVENTS = [
  "message_approved", "message_rejected", "message_sent", "review_requested", "assign_ownership", "closure_proposed",
  "reply_received", "reply_classified", // D13: written only by system:triage
];
/** Backend roles that may confirm a patient's contact details (`contact_updated`; the API's EVENT_POLICY). `clinician` is the UI's own alias. */
const CONTACT_UPDATE_ROLES = new Set(["coordinator", "primary_care_owner", "specialist_owner", "supervisor", "clinician"]);

/** Backend roles that are owner-scoped (A5): they act only on cases they own. */
const OWNER_BACKEND_ROLES = new Set(["primary_care_owner", "specialist_owner"]);
/** Roles that count as the clinician for reopen (the UI's own "clinician" prefix is still accepted by the mock). */
const REFUSAL_ROLES = new Set(["coordinator", "primary_care_owner", "specialist_owner", "clinician"]);
const PHYSICIAN_ROLES = new Set(["primary_care_owner", "specialist_owner", "clinician"]);
const COUNSELLING_REF = /^DocumentReference\/[A-Za-z0-9-]{1,64}$/;
const PHYSICIAN_IDENTITY = /^(Practitioner\/[A-Za-z0-9-]{1,64}|(primary_care_owner|specialist_owner|clinician):.{1,64})$/;
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
    if (SERVICE_ONLY_EVENTS.includes(type)) {
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

  if (type === "reply_received" || type === "reply_classified") {
    if (actor !== TRIAGE_ACTOR) throw new ApiError(409, `actor '${actor}' may not emit '${type}': only ${TRIAGE_ACTOR} writes it`);
  } else if (type === "review_requested") {
    const reason = payload.reason;
    if (!(REVIEW_REASONS as readonly string[]).includes(String(reason))) {
      throw new ApiError(422, `review_requested reason must be one of ${JSON.stringify(REVIEW_REASONS)}, got ${JSON.stringify(reason)}`);
    }
    if (REVIEW_EMITTER[String(reason)] !== actor) {
      throw new ApiError(409, `review_requested with reason '${String(reason)}' is emitted only by '${REVIEW_EMITTER[String(reason)]}'`);
    }
  } else if (type === "contact_updated") {
    if (!ctx.internal && !CONTACT_UPDATE_ROLES.has(actorRole(actor))) {
      throw new ApiError(403, `role '${actorRole(actor)}' may not confirm the patient's contact details`);
    }
    if (payload.contact_valid !== true) throw new ApiError(422, "contact_updated says the contact details are valid: contact_valid must be true");
  } else if (type === "message_sent" && payload.auto === true) {
    const holds = outreachHoldsOf(events);
    if (holds.length) throw new ApiError(409, `message_sent: automatic messages are held for this case (${holds.join(", ")}): a person must clear the flag first`);
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

  if (type === "record_informed_refusal" || type === "appointment_cancelled") {
    if (!ctx.internal && !REFUSAL_ROLES.has(actorRole(actor))) throw new ApiError(403, `role '${actorRole(actor)}' may not emit '${type}'`);
  }
  if (type === "record_informed_refusal") {
    if (!COUNSELLING_REF.test(String(payload.counselling_ref ?? ""))) throw new ApiError(422, "counselling_ref must be a DocumentReference/<id>");
    if (!PHYSICIAN_IDENTITY.test(String(payload.counselled_by ?? ""))) throw new ApiError(422, "counselled_by must be a physician (an owner or specialist) or their Practitioner");
    if (!(REFUSAL_REASONS as readonly string[]).includes(String(payload.reason))) {
      throw new ApiError(422, `record_informed_refusal reason must be one of ${JSON.stringify(REFUSAL_REASONS)}`);
    }
    if ((payload.reason === "other_with_note") !== Boolean(payload.note)) throw new ApiError(422, "a note goes with reason other_with_note, and that reason needs it");
  } else if (type === "appointment_cancelled") {
    if (!(APPOINTMENT_CANCEL_REASONS as readonly string[]).includes(String(payload.reason))) {
      throw new ApiError(422, `appointment_cancelled reason must be one of ${JSON.stringify(APPOINTMENT_CANCEL_REASONS)}`);
    }
    if (typeof payload.by_patient !== "boolean") throw new ApiError(422, "appointment_cancelled needs by_patient (true or false)");
  }
  if (type === "close") {
    const declined = payload.outcome === "declined";
    if (declined !== (state === "declined_pending_signoff")) {
      throw new ApiError(409, "close: outcome 'declined' is for a case waiting for the sign-off of an informed refusal, and only for that");
    }
    if (declined) {
      const refusal = refusalOf(events);
      if (!PHYSICIAN_ROLES.has(actorRole(actor))) throw new ApiError(409, "close (declined): signed by a physician (primary_care_owner or specialist_owner)");
      if (!refusal || payload.evidence_ref !== refusal.counselling_ref) throw new ApiError(409, "close (declined): evidence_ref must be the counselling record of the refusal");
      const counsellor = personId(refusal.counselled_by);
      if (counsellor === personId(actor) || counsellor === personId(String(payload.signed_by ?? ""))) {
        throw new ApiError(409, "close (declined): the physician who signs must not be the one who counselled the patient");
      }
    }
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
 * drafts the API did not return (a role that may not list them) are rebuilt from the `message_drafted` events,
 * which carry no text, so the panel shows "draft text unavailable".
 */
export function draftViews(records: DraftRecord[], events: CaseEvent[]): DraftView[] {
  const byId = new Map(records.map((r) => [r.draft_id, r]));
  for (const ev of events) {
    if (ev.type !== "message_drafted") continue;
    const id = String(ev.payload.draft_id);
    if (byId.has(id)) continue;
    // The event carries no text (A14, ai-boundary.md); the text only ever comes from GET /cases/{id}/drafts.
    const lang: Lang = ev.payload.language === "ar" ? "ar" : "en";
    byId.set(id, {
      draft_id: id, case_id: "", purpose: "", channel: "", language: lang,
      text: "",
      text_ar: null, text_en: null,
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
