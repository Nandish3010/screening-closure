/**
 * API CONTRACT between the web app (Cut C) and the backend.
 *
 * This file is the single source of truth for every shape the UI sends or expects.
 * The `http` adapter (httpAdapter.ts) speaks exactly these shapes against VITE_API_URL;
 * the `mock` adapter (mockAdapter.ts) implements the same `ApiClient` interface in the
 * browser. The backend agent should match the comments marked "BACKEND:".
 *
 * Status legend on each endpoint:
 *   LIVE    exists in the API today (api/app/routers/cases.py)
 *   PENDING being added by the backend agent (ticket A2); the UI codes against it and the mock
 *           implements it. The http adapter degrades gracefully where noted.
 *
 * Vocabulary: the event names and payloads below are the backend's canonical vocabulary
 * (ticket A2). Nothing in the web client uses any other spelling.
 *
 * Auth (BACKEND): every request except `GET /health` and `POST /auth/dev-login` carries
 * `Authorization: Bearer <token>`. The token comes from `POST /auth/dev-login {role, name}`
 * -> `{access_token: string, token_type: "bearer"}` (the adapter also accepts `{token}`).
 * The http adapter logs in at start-up and again on every role switch in the header, and
 * re-logs-in once and retries when it gets a 401. A 404 from dev-login means "auth not
 * enabled" and the adapter carries on without a token. The mock adapter ignores all of this.
 *
 * Identity (BACKEND, A14, ADR-019): dev-login returns `{token, token_type, user_id, role, display_name,
 * patient_ref?, provider?}` and `GET /me` returns `{user_id, role, display_name, provider?}`. `user_id` is an
 * opaque 12-hex slug (uuid5 of role + name), NOT the name. The event `actor` is `role:user_id` (for example
 * `primary_care_owner:3f9a1c7b2d04`), or `system:<service>` for the scheduler, watchdog, notifier and intake.
 * The UI never builds an actor string and never sends one (the API rejects `actor` in a body with 422); it
 * shows names from `display_name`: the case routes return `owner` as `OwnerRef` and an `actors` map
 * (actor -> OwnerRef) covering every actor in the log. Names exist only in the API's `users` table, never
 * in the log. Payloads are strict (A14): a key that is not listed under "Payload conventions" is rejected
 * with 422, `reason` fields take only the taxonomies, and `note` (at most 200 characters) is the one
 * free-text field. Do not put patient identifiers or clinical detail in a note: the log cannot be erased.
 *
 * All timestamps are ISO-8601 strings in SIMULATED time (the programme clock, not the
 * wall clock). The UI never calls `Date.now()` for business logic; it uses `sim_time`
 * from GET /metrics/closure (or `sim_time` echoed by the mock).
 */

// ---------------------------------------------------------------------------------------
// States and event vocabulary
// ---------------------------------------------------------------------------------------

/** Main path, in order. Matches config/pathways/colorectal_fit.yaml `states`. */
export const MAIN_STATES = [
  "result_received",
  "acknowledged",
  "owner_accepted",
  "patient_contacted",
  "patient_acknowledged",
  "confirmatory_booked",
  "confirmatory_completed",
  "clinician_reviewed",
  "closed",
] as const;

/**
 * Side states from the pathway YAML. BACKEND: which events reach them (`reopen` -> reopened,
 * `mark_lost_contact` -> lost_contact, ...) is the engine's call; the UI renders whatever `state` it returns.
 * The mock only implements closed --reopen--> reopened --accept_ownership--> owner_accepted.
 */
export const SIDE_STATES = [
  "overdue",
  "escalated",
  "referral_rejected",
  "consent_withdrawn",
  "reopened",
  "lost_contact",
] as const;

export type CaseState = (typeof MAIN_STATES)[number] | (typeof SIDE_STATES)[number];

/** Events that move a case along the main path (the state machine's `event` column). */
export const TRANSITION_EVENTS = [
  "acknowledge",
  "accept_ownership",
  "contact_patient",
  "patient_acknowledge",
  "book_confirmatory",
  "complete_confirmatory",
  "clinician_review",
  "close",
] as const;
export type TransitionEvent = (typeof TRANSITION_EVENTS)[number];

/**
 * Events that do NOT move the case along the main path (annotations on the log).
 *
 * BACKEND: `POST /cases/{id}/events` must accept the human-posted ones on any non-closed
 * case (and `reopen` on a closed one) and append them without a main-path transition.
 *
 * Posted by the UI (a human or the patient):
 *  handoff_requested   payload {to_provider: string}                  actor: coordinator/owner
 *  escalate            payload {rung: EscalationRung, target_role: string}
 *                                                                     actor: a human (the scheduler
 *                                                                     also emits it: "system:scheduler")
 *  escalation_accepted payload {rung: EscalationRung}                 actor: a human. The ticket gives
 *                      no payload for it; the UI sends the rung it accepts, the backend may ignore it.
 *  callback_requested  payload {reason: "patient_does_not_understand", lang: "en"|"ar"}
 *                                                                     actor: "patient:<case_id>"
 *  consent_updated     payload {channel: ContactChannel, share_with_specialist: boolean}
 *                                                                     actor: "patient:<case_id>"
 *  reopen              payload {reason: string}                       actor: "clinician:<name>" ONLY,
 *                      only on a closed case. The UI shows the resulting `state` the backend returns.
 *  mark_lost_contact   payload {} (none specified)                    actor: coordinator/owner
 *
 * Written by the backend (the UI only displays them):
 *  handoff_accepted    payload {} (optional {provider})               actor: "provider:<name>"
 *  handoff_rejected    payload {reason: HandoffRejectReason}          actor: "provider:<name>"
 *  message_drafted     payload {draft_id, provider, fallback_used, prompt_hash, language}
 *                                                                     actor: "model:<provider>"
 *                      (written when POST /cases/{id}/drafts succeeds). BACKEND (A14): the event carries NO
 *                      text. The draft text stays in the `drafts` table and the web reads it with
 *                      GET /cases/{id}/drafts (route owned by the AI cut, D4; not in main yet). To show a
 *                      still-pending draft after a reload the UI calls that route with the event's
 *                      `draft_id`; if it is unavailable the UI says "draft text unavailable, request a
 *                      new draft".)
 *  message_approved    payload {draft_id}                             actor: the approving human
 *                      (written when POST /drafts/{id}/approve succeeds; the approver is the actor)
 *  message_sent        payload {draft_id}                             actor: "system:<sender>"
 *                      (emitted by the backend AFTER message_approved. A message is never sent
 *                      without a preceding approval; the UI shows "approved, not sent yet" until it appears)
 *  mark_overdue        payload {state: string, due_at: string}        actor: "system:scheduler"
 *  watchdog_flagged    payload {reason: "idle"|"scheduler_down", idle_days?: number, last_activity_at?: string}
 *                                                                     actor: "system:watchdog" (BACKEND, A11: only
 *                      reason "idle" is written on a case; a dead scheduler is a system alert, GET /alerts)
 *  case_created        payload {patient_ref?}                         actor: "system:intake"
 *
 * Actor strings are `role:name` (`coordinator:amal`, `clinician:dr_ali`, `system:scheduler`,
 * `model:drafter`) or a bare `watchdog`. "Automated" means the role part is one of
 * system | model | watchdog | scheduler (see isAutomatedActor in lib/derive.ts).
 *
 * Payload conventions on transition events (the UI sends these). BACKEND (A14): the API accepts exactly
 * these keys per event type and nothing else (app/payloads.py is the authority; every human event also
 * accepts `note?: string` of at most 200 characters):
 *  accept_ownership      {provider?: string}                          (the owner is the caller; send no name)
 *  contact_patient       {channel?: ContactChannel | "letter", draft_id?: string (uuid)}
 *  patient_acknowledge   {lang?: "en"|"ar"}
 *  book_confirmatory     {appointment_ref?: string}                   (an id like "Appointment/a1", no spaces)
 *  complete_confirmatory {result_ref?: string}                        (confirmatory result reference)
 *  clinician_review      {signed_by?: string}                         (your own actor or "Practitioner/<user_id>")
 *  close                 {evidence_ref: string, signed_by: string}   (REQUIRED). BACKEND (A15): evidence_ref is
 *                        "DiagnosticReport/<id>" or "Procedure/<id>" and must exist in the FHIR store;
 *                        signed_by must be the caller's own actor ("role:user_id") or "Practitioner/<user_id>"
 *                        with the caller's `user_id` from GET /me, else 403 (signer) or 422 (evidence)
 *  reopen                {reason: "new_finding"|"evidence_inadequate"|"closed_in_error"|"patient_request"|"other"}
 *  reassign              {owner: string ("role:user_id" of the new owner), provider?: string}
 *  callback_requested    {reason?: "patient_does_not_understand"|"wants_to_speak_to_clinician"|"cannot_attend"|"other", lang?}
 *  mark_lost_contact     {failed_attempts: number}
 */
export type EventType = TransitionEvent | NonTransitionEvent;
export type NonTransitionEvent =
  | "case_created"
  | "handoff_requested"
  | "handoff_accepted"
  | "handoff_rejected"
  | "escalate"
  | "escalation_accepted"
  | "callback_requested"
  | "consent_updated"
  | "message_drafted"
  | "message_approved"
  | "message_sent"
  | "mark_overdue"
  | "watchdog_flagged"
  | "mark_lost_contact"
  | "reopen";

/** Fixed taxonomy for `handoff_rejected.reason`. */
export const HANDOFF_REJECT_REASONS = ["capacity", "wrong_service", "missing_info", "patient_declined"] as const;
export type HandoffRejectReason = (typeof HANDOFF_REJECT_REASONS)[number];

/** `escalate.rung`. Levels 1..3 in `escalation_level` map to these in order (0 = none). */
export const ESCALATION_RUNGS = ["reminder", "supervisor", "coordinator"] as const;
export type EscalationRung = (typeof ESCALATION_RUNGS)[number];

export const WATCHDOG_REASONS = ["idle", "scheduler_down"] as const;
export type WatchdogReason = (typeof WATCHDOG_REASONS)[number];

export interface WatchdogFlag {
  reason: WatchdogReason;
  idle_days?: number;
  at: string;
}

export type ContactChannel = "phone" | "sms" | "whatsapp";
export type Lang = "en" | "ar";

// ---------------------------------------------------------------------------------------
// Cases
// ---------------------------------------------------------------------------------------

/** One row of the append-only log. LIVE shape (see _event_json in routers/cases.py). */
export interface CaseEvent {
  seq: number;
  event_id?: string;
  type: EventType | string;
  at: string;
  actor: string;
  payload: Record<string, unknown>;
}

/**
 * GET /cases?state=&owner=&overdue=true   (read model from ticket A2)
 *
 * BACKEND: `owner` filters on exact owner name; `overdue=true` keeps only rows with
 * `overdue: true`. The UI re-applies the filters and the sort on the client, so a backend
 * that ignores them still works. The UI computes "N days overdue" itself from `due_at` and
 * the simulated clock (`sim_time`); there is no `overdue_days` field any more.
 */
/**
 * A person or service behind an actor string (A14). BACKEND: the case routes resolve `owner` to this
 * shape from the `users` table. Until the web adapter maps it, `CaseSummary.owner` below keeps the
 * display name only (the adapter should read `owner.display_name`).
 */
export interface OwnerRef {
  /** The actor string as written in the log: "role:user_id" or "system:<service>". */
  actor: string;
  display_name: string;
  role: string;
}

export interface CaseSummary {
  case_id: string;
  pathway: string;
  state: CaseState | string;
  created_at: string;
  updated_at: string;
  /**
   * Name of the person who accepted ownership, or null when unowned.
   * BACKEND (A9/A14): the API returns `owner` as `OwnerRef | null` (`{actor, display_name, role}`);
   * the adapter shows `owner.display_name` and filters with `owner=<actor or user_id>`.
   */
  owner: string | null;
  /** When the SLA for the current state expires (sim time). null when no SLA applies (e.g. closed). */
  due_at: string | null;
  /** True once `due_at` has passed in sim time (the scheduler's `mark_overdue`). */
  overdue: boolean;
  /** 0 = none, 1 = reminder, 2 = supervisor, 3 = coordinator (highest rung reached in this state). */
  escalation_level: 0 | 1 | 2 | 3;
  /** True while patient consent is frozen: nobody may contact the patient until it is re-confirmed. */
  consent_frozen: boolean;
  /** Timestamp of the newest event on the case. */
  last_event_at: string;
  /**
   * ASK to the backend (not in the A2 list): the open watchdog flag, if any, so the worklist can
   * show "nobody is looking at this" without fetching every case. Optional; when absent the
   * worklist simply shows no marker, and the case page derives it from the events.
   */
  watchdog_flagged?: WatchdogFlag | null;
  /** 0..1 loss-to-follow-up risk. Optional. ONLY used to order the worklist; never acts. */
  risk_score?: number;
}

export interface CaseListParams {
  state?: string;
  owner?: string;
  overdue?: boolean;
}

/** GET /cases/{id}: the summary fields plus the full event log. */
export interface CaseDetail extends Omit<CaseSummary, "pathway" | "created_at" | "updated_at"> {
  pathway?: string;
  created_at?: string;
  updated_at?: string;
  events: CaseEvent[];
}

/** Body of POST /cases/{id}/events (LIVE). Returns the updated CaseDetail (201). */
export interface PostEventBody {
  type: EventType | string;
  actor: string;
  payload?: Record<string, unknown>;
  /** Optional idempotency key; the UI always sends one so retries are safe. */
  event_id?: string;
}

// ---------------------------------------------------------------------------------------
// Timeline  (PENDING)
// ---------------------------------------------------------------------------------------

/**
 * GET /cases/{id}/timeline -> { case_id, sim_time, items: TimelineItem[] }
 *
 * BACKEND (A9, live): the response is `{case_id, sim_time, items, actors}`. Items are sorted by `at`
 * ascending. `kind: "event"` items are the raw log rows. The API derives these from the log and
 * interleaves them (they are not stored): `sla_started` (`at` = clock start, with `state, sla, working_days,
 * due_at, clock_start`), `sla_breached` (`at` = the due date, `detected_at` = when the sweep wrote it;
 * the type below calls it `sla_breach`), `escalation` (`rung, level, target_role, after_breach_days,
 * accepted, accepted_at, accepted_by`), `handoff_bounce` (`to_provider, returned_to, requested_at, reason`)
 * and `watchdog_flag` (`reason, idle_days, cleared_at`). `actors` maps each actor string to
 * `{actor, display_name, role}`. The older `sla_breach` / `escalation` shapes below are the mock's. The UI also derives SLA clocks, handoffs, consent state and
 * AI-draft provenance, overdue and watchdog markers from the `event` items alone, so a timeline with only events
 * degrades gracefully.
 */
export type TimelineItem = TimelineEvent | TimelineSlaBreach | TimelineEscalation;

export interface TimelineEvent extends CaseEvent {
  kind: "event";
}

export interface TimelineSlaBreach {
  kind: "sla_breach";
  at: string;
  /** SLA key from the pathway YAML, e.g. "clinician_review". */
  sla: string;
  due_at: string;
  breach_days: number;
}

export interface TimelineEscalation {
  kind: "escalation";
  at: string;
  /** The ladder rung, same vocabulary as the `escalate` event. */
  rung: EscalationRung;
  /** Who is addressed, e.g. "owner", "supervisor", "coordinator". */
  target_role: string;
  after_breach_days: number;
  /** True once a human accepted it (`escalation_accepted`); undefined for the plain reminder rung. */
  accepted?: boolean;
}

export interface CaseTimeline {
  case_id: string;
  sim_time: string;
  items: TimelineItem[];
}

// ---------------------------------------------------------------------------------------
// AI drafts  (PENDING)  - governed edge: the model drafts, a human approves.
// ---------------------------------------------------------------------------------------

/**
 * POST /cases/{id}/drafts   body: {requested_by: string}
 * -> 201 Draft. BACKEND: also appends a `message_drafted` event (see vocabulary above).
 * `fallback_used` is true when the deterministic template was used instead of the model.
 */
export interface Draft {
  draft_id: string;
  text_ar: string;
  text_en: string;
  provider: string;
  fallback_used: boolean;
}

/**
 * POST /drafts/{draft_id}/approve   body: {approved_by: string}
 * -> 200 {draft_id, approved_by, approved_at}. BACKEND: appends `message_approved` to the owning
 * case and then emits `message_sent`. A draft is never "sent" until this succeeds.
 */
export interface DraftApproval {
  draft_id: string;
  approved_by: string;
  approved_at: string;
}

// ---------------------------------------------------------------------------------------
// Metrics  (PENDING)
// ---------------------------------------------------------------------------------------

export type AgeBucket = "0-30" | "31-60" | "61-90" | "90+";

/**
 * GET /metrics/closure (coordinator, supervisor). BACKEND (A10, live): beyond the fields below the API returns
 * `cases_total, cases_open, cases_closed, closures_measured, unsafe_closures, p50_working_days,
 * p90_working_days, lost_to_follow_up, lost_never_surfaced, cases_flagged` and
 * `abstention: {attempts, abstentions, rate} | null`; `bottleneck_by_transition[].transition` is "a -> b"
 * (the state left and entered) and each row also has `median_working_days` and `n`; `p50_days` and
 * `p90_days` are null while nothing has closed. Definitions: api/app/metrics.py.
 */
export interface ClosureMetrics {
  p50_days: number;
  p90_days: number;
  open_by_age_bucket: Record<AgeBucket, number>;
  breaches_by_provider: { provider: string; count: number }[];
  bottleneck_by_transition: { transition: string; median_days: number }[];
  /** Simulated "now" (ISO-8601). Shown in the permanent header badge on every page. */
  sim_time: string;
}

// ---------------------------------------------------------------------------------------
// Identity  (PENDING)
// ---------------------------------------------------------------------------------------

export type Role = "coordinator" | "owner" | "specialist" | "clinician" | "patient";
export const ROLES: Role[] = ["coordinator", "owner", "specialist", "clinician", "patient"];

/**
 * GET /me -> {role, name}: the identity behind the bearer token. The header's role switcher is a
 * demo override: switching calls `login(role, name)` (POST /auth/dev-login) and the adapter swaps tokens.
 */
export interface Me {
  role: Role;
  name: string;
  /** BACKEND (A14): the API's `user_id` (opaque slug) and `display_name`; the adapter maps display_name to `name`. */
  user_id?: string;
}

// ---------------------------------------------------------------------------------------
// The client interface both adapters implement
// ---------------------------------------------------------------------------------------

export interface ApiClient {
  /** "mock" | "http" - for display only. */
  readonly adapter: "mock" | "http";
  health(): Promise<{ status: string }>;
  /**
   * Switch identity. http: POST /auth/dev-login {role, name}, keep the bearer token and attach it
   * to every later request (requests wait for a login in flight). mock: no-op, it ignores auth.
   */
  login(role: Role, name: string): Promise<void>;
  me(): Promise<Me>;
  listCases(params?: CaseListParams): Promise<CaseSummary[]>;
  getCase(caseId: string): Promise<CaseDetail>;
  getTimeline(caseId: string): Promise<CaseTimeline>;
  /** Returns the updated case. Rejects with ApiError(status 409) on an invalid transition. */
  postEvent(caseId: string, body: PostEventBody): Promise<CaseDetail>;
  requestDraft(caseId: string, requestedBy: string): Promise<Draft>;
  approveDraft(draftId: string, approvedBy: string): Promise<DraftApproval>;
  closureMetrics(): Promise<ClosureMetrics>;
  /** Mock only: restore the seeded fixtures. No-op for http. */
  resetDemo?(): Promise<void>;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Providers offered in the hand-off picker (demo list; replace with a real directory). */
export const PROVIDERS = [
  "Al Noor Endoscopy Centre",
  "Corniche Gastro Unit",
  "Marina Digestive Health",
] as const;
