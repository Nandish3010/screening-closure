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
 * Auth (BACKEND, A8): every request except `GET /health` and `POST /auth/dev-login` carries
 * `Authorization: Bearer <token>`. The token comes from `POST /auth/dev-login {role, name, provider?}`
 * -> `{token, token_type: "bearer", user_id, role, display_name, provider?}` (`user_id` is `role:name`;
 * the adapter also accepts `{access_token}`). `role` is a BACKEND role (see BACKEND_ROLE below: the
 * UI's "owner" logs in as `primary_care_owner`, "specialist" as `specialist_owner`, "clinician" as
 * `primary_care_owner`). `provider` is sent for the owner roles only (the organisation the clinician
 * acts for; it decides which handoff requests they may answer). The http adapter logs in at start-up
 * and again on every role switch in the header, and re-logs-in once and retries when it gets a 401.
 * A 404 from dev-login means "auth not enabled" and the adapter carries on without a token. The mock
 * adapter keeps the same session (so it can enforce who may approve a draft) but needs no token.
 *
 * The actor of every event is the bearer token's identity (`role:name`); `POST /cases/{id}/events`
 * answers 422 to a body that contains `actor`, so the http adapter never sends one.
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
 *  handoff_requested   payload {to_provider: string}                  actor: the case owner (or a
 *                      supervisor). Sets `pending_handoff`; ownership moves only when the provider accepts.
 *                      A clinician who is NOT the owner is refused (403, owner scoping, A5). The UI offers
 *                      them "request handoff to my provider" (to_provider = their own provider) and shows
 *                      "waiting for acceptance" from `pending_handoff`: a GUESS that the backend will let
 *                      that one request through (today it is a 403 and the UI shows the engine's reason).
 *  reassign            payload {owner: string}                        actor: coordinator or supervisor,
 *                      only from `referral_rejected` (a rejected or bounced referral); owner becomes `owner`.
 *  escalate            payload {rung: EscalationRung, state: string, target?: string}
 *                                                                     actor: a human (the scheduler
 *                                                                     also emits it: "system:scheduler")
 *  escalation_accepted payload {rung: EscalationRung}                 actor: a human. The ticket gives
 *                      no payload for it; the UI sends the rung it accepts, the backend may ignore it.
 *  callback_requested  payload {reason: "patient_does_not_understand", lang: "en"|"ar"}
 *                                                                     actor: "patient:<case_id>"
 *  consent_updated     payload: any non-empty subset of the booleans {channel_sms, channel_whatsapp,
 *                      channel_phone, channel_letter, share_with_specialist, withdrawn} (partial updates;
 *                      anything else is 422). `withdrawn: true` freezes outreach, the API then adds
 *                      `review_requested`; `withdrawn: false` restores. actor: patient (own case) or the
 *                      coordinator recording the patient's instruction.
 *  reopen              payload {reason: ReopenReason (taxonomy)}      actor: a clinician (owner role)
 *                      ONLY, only on a closed case. The UI shows the resulting `state` the backend returns.
 *  mark_lost_contact   payload {failed_attempts: number >= 3}         actor: coordinator/owner
 *
 * Written by the backend (the UI only displays them):
 *  handoff_accepted    payload {} (optional {provider})               actor: the accepting clinician
 *                      (`primary_care_owner:<id>` / `specialist_owner:<id>`); they become the owner.
 *  handoff_rejected    payload {reason: HandoffRejectReason | "timeout", bounced?: true}
 *                      actor: the receiving clinician, or `system:scheduler` for a bounce (`reason:
 *                      "timeout"`, `bounced: true`, no answer in 3 working days). Moves the case to
 *                      `referral_rejected` from patient_acknowledged / confirmatory_booked.
 *  message_drafted     payload {draft_id, provider, fallback_used, prompt_hash, language}
 *                                                                     actor: "model:<provider>"
 *                      (written when POST /cases/{id}/drafts succeeds). BACKEND (A14): the event carries NO
 *                      text. The draft text stays in the `drafts` table and the web reads it with
 *                      GET /cases/{id}/drafts (D4, live). To show a still-pending draft after a reload the
 *                      UI calls that route with the event's `draft_id`; if it is unavailable the UI says
 *                      "draft text unavailable, request a new draft". Text in a payload is ignored.)
 *  review_requested    payload {reason: "consent_withdrawn", source_event_id}
 *                                                                     actor: "system:consent"
 *                      (the API adds it when consent is withdrawn; the case is then on the human-review list)
 *  message_approved    payload {draft_id}                             actor: the approving human
 *                      (written only by POST /drafts/{id}/approve; the approver is the actor. It cannot
 *                      be posted to POST /cases/{id}/events: 403 for every role)
 *  message_rejected    payload {draft_id, reason: RejectReason}       actor: the rejecting human
 *                      (A16; written only by POST /drafts/{id}/reject, never postable: 403 for every role.
 *                      The reason is a fixed taxonomy; the note for `other_with_note` stays on the draft row)
 *  message_sent        payload {draft_id, notification_id, channel, language, purpose}
 *                                                                     actor: "system:notifier"
 *                      (emitted by the backend AFTER message_approved. A message is never sent
 *                      without a preceding approval; the UI shows "approved, not sent yet" until it appears.
 *                      Absent after an approval whose channel was blocked: see DraftApproval.sent)
 *  mark_overdue        payload {state: string, due_at: string}        actor: "system:scheduler"
 *                      or, for a milestone breach (A18), {milestone: string, due_at: string} with no `state`
 *  watchdog_flagged    payload {reason: "idle"|"scheduler_down", idle_days?: number, last_activity_at?: string}
 *                                                                     actor: "system:watchdog" (BACKEND, A11: only
 *                      reason "idle" is written on a case; a dead scheduler is a system alert, GET /alerts)
 *  case_created        payload {patient_ref?}                         actor: "system:intake"
 *
 * Actor strings are `role:name` with a BACKEND role (`coordinator:amal`, `primary_care_owner:dr_ali`,
 * `specialist_owner:dr_s`, `system:scheduler`, `model:template`, `system:notifier`, `system:consent`) or a
 * bare `watchdog`. "Automated" means the role part is one of
 * system | model | watchdog | scheduler (see isAutomatedActor in lib/derive.ts).
 *
 * Payload conventions on transition events (the UI sends these). BACKEND (A14): the API accepts exactly
 * these keys per event type and nothing else (app/payloads.py is the authority; every human event also
 * accepts `note?: string` of at most 200 characters):
 *  accept_ownership      {provider?: string}                          (the owner is the caller; send no name)
 *  contact_patient       {channel?: ContactChannel | "letter", draft_id?: string (uuid)}
 *  patient_acknowledge   {lang?: "en"|"ar"}
 *  book_confirmatory     {appointment_ref?: string, appointment_date?: "YYYY-MM-DD" | ISO date-time}
 *                                                                     (ref is an id like "Appointment/a1", no spaces; the date is what an
 *                                                                      appointment-confirmation draft states, D4)
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
  | "message_rejected"
  | "message_sent"
  | "mark_overdue"
  | "watchdog_flagged"
  | "mark_lost_contact"
  | "reassign"
  | "review_requested"
  | "reopen";

/** Fixed taxonomy for `handoff_rejected.reason`. */
export const HANDOFF_REJECT_REASONS = ["capacity", "wrong_service", "missing_info", "patient_declined"] as const;
export type HandoffRejectReason = (typeof HANDOFF_REJECT_REASONS)[number];

/** `escalate.rung`. Levels 1..3 in `escalation_level` map to these in order (0 = none). */
export const ESCALATION_RUNGS = ["reminder", "supervisor", "coordinator"] as const;
export type EscalationRung = (typeof ESCALATION_RUNGS)[number];

export const WATCHDOG_REASONS = ["idle", "scheduler_down"] as const;
export type WatchdogReason = (typeof WATCHDOG_REASONS)[number];

/**
 * BACKEND (A11, live): \`GET /alerts\` (coordinator, supervisor) -> \`SystemAlert[]\`, newest first (\`?active=true\` keeps the
 * open ones); \`GET /heartbeats\` -> \`{scheduler, watchdog}\` each \`{service, last_beat_at, age_seconds, ...} | null\`. These are wall-clock
 * times (real seconds), not simulated time: a dead scheduler is a fact about the running system. A case is flagged idle with
 * \`watchdog_flagged\` (reason "idle") and \`GET /cases\` returns the open one as \`watchdog_flagged\`; a dead scheduler is NOT a case
 * event, it is an alert here.
 */
export interface SystemAlert {
  id: number | null;
  kind: "scheduler_down" | "scheduler_failing" | "watchdog_down" | string;
  /** Wall-clock ISO time the alert opened. */
  since: string;
  /** Null while the alert is open. */
  cleared_at: string | null;
  derived?: boolean;
  detail: Record<string, unknown>;
}

export interface WatchdogFlag {
  reason: WatchdogReason;
  idle_days?: number;
  at: string;
}

export type ContactChannel = "phone" | "sms" | "whatsapp" | "letter";
/** Channels a draft can be written for (`POST /cases/{id}/drafts`). */
export type DraftChannel = "sms" | "whatsapp" | "letter";
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
 * The owner as the read model reports it. BACKEND (A14, in progress): `{actor, display_name, role}`.
 * Today `owner` is the actor string (`primary_care_owner:Dr Sara Khan`) or, in the old mock, a bare name.
 * The adapters accept both and normalise: `CaseSummary.owner` is always the display name (or null) and
 * `owner_actor` / `owner_role` carry the rest, so the UI never branches on the wire shape.
 */
export interface OwnerObject {
  actor: string;
  display_name: string;
  role: string;
}
export type RawOwner = string | OwnerObject | null;

/** Consent as the read model reports it (A6): everything allowed until the patient says otherwise. */
export interface Consent {
  channel_sms: boolean;
  channel_whatsapp: boolean;
  channel_phone: boolean;
  channel_letter: boolean;
  share_with_specialist: boolean;
  withdrawn: boolean;
  updated_at?: string | null;
  updated_by?: string | null;
}
export const CONSENT_KEYS = [
  "channel_sms", "channel_whatsapp", "channel_phone", "channel_letter", "share_with_specialist", "withdrawn",
] as const;
export type ConsentKey = (typeof CONSENT_KEYS)[number];
/** A `consent_updated` payload: any non-empty subset of the six booleans. */
export type ConsentChanges = Partial<Record<ConsentKey, boolean>>;
export interface ConsentHistoryEntry {
  at: string;
  actor: string;
  changes: ConsentChanges;
}

/** `pending_handoff` on the read model while a request is unanswered, else null (A5). */
export interface PendingHandoff {
  to_provider: string;
  requested_by: string;
  from_owner: string | null;
  requested_at: string;
}
/** `last_handoff`: the latest answer. A scheduler bounce has `reason: "timeout"`, `bounced: true`. */
export interface LastHandoff {
  outcome: "accepted" | "rejected";
  to_provider: string;
  by: string;
  at: string;
  reason?: string;
  bounced?: boolean;
}

/**
 * GET /cases?state=&owner=&overdue=true&flagged=true   (read model from tickets A2, A9, A11)
 *
 * BACKEND: `owner` filters on exact owner name; `overdue=true` keeps only rows with `overdue: true`;
 * `flagged=true` keeps only rows with an open `watchdog_flagged`. The UI re-applies the filters and the
 * sort on the client, so a backend that ignores them still works. The UI computes "N days overdue"
 * itself from `due_at` and the simulated clock (`sim_time`); there is no `overdue_days` field any more.
 * The real `GET /cases/{id}` nests these fields under `read_model`; the http adapter lifts them.
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
  /** Actor string of the owner (`primary_care_owner:Dr Sara Khan`) when the wire gave one. */
  owner_actor?: string | null;
  /** Role label of the owner when the wire gave one (A14). */
  owner_role?: string | null;
  /** When the SLA for the current state expires (sim time). null when no SLA applies (e.g. closed). */
  due_at: string | null;
  /** True once `due_at` has passed in sim time (the scheduler's `mark_overdue`). */
  overdue: boolean;
  /** 0 = none, 1 = reminder, 2 = supervisor, 3 = coordinator (highest rung reached in this state). */
  escalation_level: 0 | 1 | 2 | 3;
  /** True while patient consent is frozen (withdrawn): nobody may contact the patient until it is restored. */
  consent_frozen: boolean;
  /** Timestamp of the newest event on the case. */
  last_event_at: string;
  /**
   * ASK to the backend (A11): the open watchdog flag, if any, so the worklist and the dashboard can show
   * "nobody is looking at this" without fetching every case. null / absent = not flagged.
   */
  watchdog_flagged?: WatchdogFlag | null;
  /** The provider the current owner acts for (set by an accepted handoff). */
  provider?: string | null;
  pending_handoff?: PendingHandoff | null;
  last_handoff?: LastHandoff | null;
  /** 0..1 loss-to-follow-up risk. Optional. ONLY used to order the worklist; never acts. */
  risk_score?: number;
}

export interface CaseListParams {
  state?: string;
  owner?: string;
  overdue?: boolean;
  /** Only cases with an open watchdog flag ("cases nobody is looking at"). */
  flagged?: boolean;
}

/**
 * GET /cases/{id}: the summary fields plus consent, review state and the full event log.
 *
 * For a PATIENT login the API answers a redacted case (SEC-2, `readmodel.patient_case_json`): `owner` is
 * `{actor: <role>, display_name: <role>, role: <role>}` (the role only: `primary_care_owner`, `specialist_owner`,
 * `coordinator`...), every event `actor` is a bare role (no id, no name), only the allow-listed event types and payload
 * keys are present, and there are no SLA, escalation, risk, milestone or watchdog fields. After normalisation that is
 * `owner === owner_actor === owner_role === <role>`; a staff-shaped owner has `owner_actor` of the form `role:id`.
 * `patientOwnerOf` (lib/derive.ts) tells the two apart and the patient page says "your primary care doctor".
 */
export interface CaseDetail extends Omit<CaseSummary, "pathway" | "created_at" | "updated_at"> {
  pathway?: string;
  created_at?: string;
  updated_at?: string;
  /** A6: current consent (defaults: everything allowed, not withdrawn). */
  consent?: Consent;
  consent_history?: ConsentHistoryEntry[];
  /** `consent_withdrawn` while the case is on the human-review list, else null. */
  review_reason?: string | null;
  /** A18: referral within 15 working days of the result, colonoscopy within 31 working days of referral. */
  milestones?: Partial<Record<MilestoneName, MilestoneClock>>;
  events: CaseEvent[];
}

/** The two clocks that span states (A18, ADR-028); the names are the keys of `milestones` in the pathway YAML. */
export type MilestoneName = "referral_requested" | "confirmatory_completed";

/**
 * One milestone on `GET /cases/{id}` (`milestones` at the top level of the response). `due_at` is null while the
 * clock has not started (the colonoscopy clock starts at the referral acceptance). `overdue` is the milestone's own
 * breach flag: true from the scheduler's `mark_overdue {milestone}` until the milestone is met.
 */
export interface MilestoneClock {
  due_at: string | null;
  met_at: string | null;
  overdue: boolean;
  working_days?: number;
  started_at?: string | null;
  /** A met milestone was met after its due date. */
  met_late?: boolean | null;
  /** The case closed before the milestone was met. */
  void?: boolean;
  escalation_level?: number;
}

/** Body of POST /cases/{id}/events (LIVE). Returns the updated CaseDetail (201). */
export interface PostEventBody {
  type: EventType | string;
  /** Mock only. The backend derives the actor from the bearer token and rejects a body that has one. */
  actor: string;
  payload?: Record<string, unknown>;
  /** Optional idempotency key; the UI always sends one so retries are safe. */
  event_id?: string;
}

// ---------------------------------------------------------------------------------------
// Timeline  (PENDING, A9)
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
 * `watchdog_flag` (`reason, idle_days, cleared_at`) and `backdated` (`event_id, event_type, previous_at,
 * behind_seconds`: an event accepted with an `at` before the previous event's, ADR-027; the case read model
 * also carries `backdated_events`, the count). `actors` maps each actor string to
 * `{actor, display_name, role}`. The older `sla_breach` / `escalation` shapes below are the mock's. The UI also derives SLA clocks, handoffs, consent state and
 * AI-draft provenance, overdue and watchdog markers from the `event` items alone, so a timeline with only events
 * degrades gracefully.

 * When both a derived item and the underlying event exist at the same `at` (a bounce, a watchdog flag),
 * the UI shows the derived item once.
 */
export type TimelineItem =
  | TimelineEvent | TimelineSlaStarted | TimelineSlaBreached | TimelineEscalation | TimelineBounce | TimelineWatchdog | TimelineBackdated
  | TimelineMilestone;

/**
 * A18: a milestone clock began (`milestone_started`, `at` = the start, with `working_days`, `due_at`), was breached
 * (`milestone_breached`, `at` = the due date, `detected_at` = when the sweep wrote it) or was met (`milestone_met`,
 * `late` says after the due date). Derived by the API, never stored.
 */
export interface TimelineMilestone {
  kind: "milestone_started" | "milestone_breached" | "milestone_met";
  at: string;
  milestone: MilestoneName | string;
  due_at?: string | null;
  working_days?: number;
  detected_at?: string;
  late?: boolean;
}

export interface TimelineEvent extends CaseEvent {
  kind: "event";
}

export interface TimelineSlaStarted {
  kind: "sla_started";
  at: string;
  /** SLA key from the pathway YAML, e.g. "clinician_review". */
  sla: string;
  due_at?: string;
}

export interface TimelineSlaBreached {
  kind: "sla_breached" | "sla_breach";
  at: string;
  sla: string;
  due_at?: string;
  breach_days?: number;
}

/**
 * One other ladder that an escalation also covers (A19, ADR-031): an entry of `escalate.also_for` and of the same field on
 * the timeline `escalation` item. The ladder is named like the escalation names its own, exactly one of `state` (the
 * state's SLA ladder) or `milestone` (a milestone's ladder, A18). `rung` and `level` are that ladder's rung that rode on
 * the escalation; `due_at` is that ladder's breach date and `rung_due_at` the date the rung fell due.
 */
export interface TimelineAlsoFor {
  state?: string;
  milestone?: MilestoneName | string;
  rung: EscalationRung | string;
  level: number;
  due_at: string;
  rung_due_at: string;
}

/**
 * An escalation rung, derived from an `escalate` event. The live API emits every field below; the mock's older shape has
 * only `kind`, `at` and `rung`, so everything else is optional.
 *
 * A19 (ADR-031): when two ladders of one case (the state's and a milestone's, or two milestones) need the same role on
 * the same working day, the role is told once. Two optional payload fields carry that; the API emits both keys, null
 * when absent, and they are never both set:
 *  - `also_for`: this one escalation also stands for the listed other ladders, whose rung to the same target rode on it.
 *    Those rungs have no `escalate` event of their own; the ladders share this event's id, and one
 *    `escalation_accepted` accepts them all.
 *  - `attached_to`: the event id (`event_id`) of the escalation of the same working day that this rung was attached to.
 *    This rung notifies nobody and is not a new escalation, so the UI must not alert, count or send for it again;
 *    show it as riding on the earlier escalation.
 */
export interface TimelineEscalation {
  kind: "escalation";
  at: string;
  /** The `escalate` event's id: the value that rungs attached to this escalation carry in `attached_to`. */
  event_id?: string;
  /** A18: the milestone whose ladder this rung is on; absent or null on the state's SLA ladder. */
  milestone?: MilestoneName | string | null;
  /** 1..3, the ladder level of this rung (see `ESCALATION_RUNGS`). */
  level?: number;
  /** When this rung fell due. */
  rung_due_at?: string | null;
  /** A19: the other ladders this one escalation also covers (entries below); null or absent when it covers only its own. */
  also_for?: TimelineAlsoFor[] | null;
  /** A19: event id of the same-working-day escalation this rung was attached to; it notifies nobody. Null or absent otherwise. */
  attached_to?: string | null;
  /** The ladder rung, same vocabulary as the `escalate` event. */
  rung: EscalationRung;
  /** Who is addressed, e.g. "owner", "supervisor", "coordinator". */
  target_role?: string;
  after_breach_days?: number;
  /** True once a human accepted it (`escalation_accepted`); undefined for the plain reminder rung. */
  accepted?: boolean;
}

export interface TimelineBounce {
  kind: "bounce";
  at: string;
  to_provider?: string;
  reason?: string;
}

export interface TimelineWatchdog {
  kind: "watchdog";
  at: string;
  reason?: WatchdogReason | string;
  idle_days?: number;
}

/** T-F4, ADR-027: an event accepted with an `at` earlier than the previous event's. Derived by the API, never stored. */
export interface TimelineBackdated {
  kind: "backdated";
  at: string;
  event_id?: string;
  event_type?: string;
  previous_at?: string;
  behind_seconds?: number;
}

export interface CaseTimeline {
  case_id: string;
  sim_time: string;
  items: TimelineItem[];
}

// ---------------------------------------------------------------------------------------
// AI drafts  (D4, D5) - governed edge: the model drafts, a human approves.
// ---------------------------------------------------------------------------------------

export const DRAFT_PURPOSES = ["result_notification", "reminder", "appointment_confirmation"] as const;
export type DraftPurpose = (typeof DRAFT_PURPOSES)[number];
export const DRAFT_CHANNELS: DraftChannel[] = ["sms", "whatsapp", "letter"];

/**
 * POST /cases/{id}/drafts   body: DraftRequestBody {purpose, channel, language}
 * -> 201 Draft. The requester is the bearer token's identity, never a body field (`requested_by` is a 422).
 * Roles: owners, coordinator, supervisor. 409 when the case is closed, its outreach is frozen (consent
 * withdrawn), or `appointment_confirmation` is asked for without a booked appointment date.
 * `text` is the draft in the requested `language`; `text_ar` / `text_en` hold it in that language and are
 * null in the other. `fallback_used` is true when the deterministic template was used instead of the model;
 * `abstention_reason` says why the model abstained (null when it did not). `prompt_hash` identifies the exact
 * prompt that produced the draft (the prompt itself is stored for audit).
 * BACKEND (D4): also appends `message_drafted {draft_id, provider, fallback_used, prompt_hash, language}`
 * as `model:<provider>`; the event carries no text.
 */
export interface Draft {
  draft_id: string;
  text: string;
  text_ar: string | null;
  text_en: string | null;
  language: Lang;
  provider: string;
  fallback_used: boolean;
  abstention_reason: string | null;
  prompt_hash: string;
}

export interface DraftRequestBody {
  purpose: DraftPurpose;
  channel: DraftChannel;
  language: Lang;
}

/**
 * GET /cases/{id}/drafts -> DraftRecord[] (oldest first). Owner-scoped like every other owner action (SEC-2):
 * a coordinator or supervisor reads any case's drafts; a clinician (`primary_care_owner` / `specialist_owner`)
 * only those of a case they own and gets 403 otherwise (also when the case has no owner yet). The 403 is an
 * expected answer, not a failure: the UI explains it (`DRAFTS_OWNER_ONLY`) instead of showing an error.
 * The only place the draft text lives after the request: the timeline and the drafts panel load it here.
 * `approved_*` / `rejected_*` are null until a decision. Whether an approved draft was actually sent is NOT
 * here: `message_sent {draft_id}` on the timeline says so, and the approve response says why not.
 */
export interface DraftRecord extends Draft {
  case_id: string;
  purpose: DraftPurpose | string;
  channel: DraftChannel | string;
  model_id?: string | null;
  created_by: string;
  created_at: string;
  approved_at: string | null;
  approved_by: string | null;
  rejected_at: string | null;
  rejected_by: string | null;
  rejection_reason: string | null;
}

/**
 * POST /drafts/{draft_id}/approve   NO body (the approver is the bearer token's identity)
 * -> 200 DraftApproval. BACKEND (D5): in one transaction sets approved_at/approved_by on the draft
 * and appends `message_approved {draft_id}` (actor: the approver); then the notification mock is
 * called, which appends `message_sent {draft_id, notification_id, channel, language, purpose}`
 * as `system:notifier` and writes the notification row. A draft is never "sent" until this succeeds.
 *
 * Roles: primary_care_owner / specialist_owner of THAT case, or supervisor. Never coordinator,
 * lab or patient (403). Errors: 404 unknown draft; 409 already approved or rejected, case closed,
 * or outreach frozen by a consent withdrawal (nothing is written in any 409 case).
 *
 * `sent: false` is still a 200: the approval stands (it is recorded with the outbox row in one transaction,
 * ADR-030) but the notification mock did not send. `blocked_reason` says why:
 *   "consent"    the patient's consent for that channel is off
 *   "contact"    the contact details are flagged invalid
 *   "case_state" the case changed before the send (closed, consent withdrawn); also a draft whose text is gone
 *   "pending"    the send could not finish yet (a transient failure): nothing is lost, the scheduler's next
 *                outbox drain sends it once. `notification_id` is null. This is not a block.
 * `message_sent` does not exist yet in any of these cases; for the blocked ones `notification_id` points at the
 * logged, blocked row (GET /cases/{id}/notifications has the detail). When `sent` is true, `blocked_reason` is null.
 * Any other string is possible in principle (the API passes an unknown outbox reason through): the UI shows it
 * as plain words and never crashes on it.
 * `approved_by` is the actor string, e.g. "primary_care_owner:Dr Sara Khan"; `approved_at` is
 * simulation time. `sent`, `blocked_reason` and `notification_id` are always present from the
 * backend (optional here only so older fixtures keep compiling; the UI treats a missing `sent` as true).
 */
export interface DraftApproval {
  draft_id: string;
  approved_by: string;
  approved_at: string;
  sent?: boolean;
  blocked_reason?: BlockedReason | null;
  notification_id?: string | null;
}
/** The values the approve answer carries today. `string & {}` keeps the door open for a value this UI has not met. */
export type BlockedReason = "consent" | "contact" | "case_state" | "pending" | (string & {});
export const KNOWN_BLOCKED_REASONS = ["consent", "contact", "case_state", "pending"] as const;
/** What the UI says when `GET /cases/{id}/drafts` answers 403 (the caller is a clinician who does not own the case). */
export const DRAFTS_OWNER_ONLY = "Only the case owner can see drafts for this case.";

/** Fixed taxonomy for `POST /drafts/{id}/reject` and `message_rejected.reason` (A16). */
export const REJECT_REASONS = ["inaccurate", "tone", "wrong_language", "not_needed", "other_with_note"] as const;
export type RejectReason = (typeof REJECT_REASONS)[number];
/** Longest note the API takes (every free-text field is capped at 200 characters, A14). */
export const REJECT_NOTE_MAX = 200;

/**
 * POST /drafts/{draft_id}/reject   body: {reason: RejectReason, note?: string}
 * -> 200 DraftRejection. Same roles and ownership rule as approve; allowed on a frozen case.
 * BACKEND (A16): in one transaction the draft row gets rejected_at/rejected_by/rejection_reason and the log gets
 * `message_rejected {draft_id, reason}` (actor: the rejecting human), so the rejection shows on the timeline.
 * `note` is required with `other_with_note` (at most 200 characters) and refused (422) with any other reason; it is
 * kept on the draft row only, as "other_with_note: <note>" in `rejection_reason`, never in the event.
 * 422 for a reason outside the taxonomy (free text is not accepted); 409 if the draft already has a decision or the
 * case is closed.
 */
export interface DraftRejectBody {
  reason: RejectReason;
  note?: string;
}
export interface DraftRejection {
  draft_id: string;
  rejected_by: string;
  rejected_at: string;
  reason: RejectReason;
  note?: string | null;
}

// ---------------------------------------------------------------------------------------
// Metrics and alerts  (PENDING)
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

/**
 * GET /alerts -> Alert[] (A11). The watchdog raises `scheduler_down` when the scheduler's heartbeat has been
 * missing for 60 seconds and clears it (`cleared_at`) when heartbeats resume. An alert with
 * `cleared_at: null` is active: the dashboard shows the stale-scheduler banner. `since` and `cleared_at` are
 * ISO-8601 (sim time). Roles: GUESS, any staff role.
 */
export interface Alert {
  kind: "scheduler_down" | string;
  since: string;
  cleared_at: string | null;
}

// ---------------------------------------------------------------------------------------
// Identity
// ---------------------------------------------------------------------------------------

/** The UI's roles. They map onto the backend's roles (api/app/auth.py USER_ROLES) by BACKEND_ROLE. */
export type Role = "coordinator" | "owner" | "specialist" | "clinician" | "supervisor" | "patient";
export const ROLES: Role[] = ["coordinator", "owner", "specialist", "clinician", "supervisor", "patient"];

/**
 * UI role -> backend role used for `POST /auth/dev-login`. The backend has no separate "clinician": sign-off
 * is done by the owner roles, so the UI's clinician is a second `primary_care_owner` (Dr Huda Rahman).
 */
export const BACKEND_ROLE: Record<Role, string> = {
  coordinator: "coordinator",
  owner: "primary_care_owner",
  specialist: "specialist_owner",
  clinician: "primary_care_owner",
  supervisor: "supervisor",
  patient: "patient",
};
/** Roles that act only on cases they own (A5 owner scoping): the clinician roles. */
export const OWNER_SCOPED_ROLES: readonly Role[] = ["owner", "specialist", "clinician"];

/**
 * GET /me -> `{user_id, role, display_name, patient_ref?, provider?}`; the adapter folds that into `Me`.
 * The header's role switcher is a demo override: switching calls `login(role, name)`
 * (POST /auth/dev-login) and the adapter swaps tokens.
 */
export interface Me {
  role: Role;
  name: string;
  /** BACKEND (A14): the API's `user_id` (opaque slug) and `display_name`; the adapter maps display_name to `name`. */
  user_id?: string;
  /** The provider the caller acts for (owner roles). */
  provider?: string;
  /** Patients only: the `Patient/<id>` whose case they own (decided by the login, E8). */
  patient_ref?: string;
}

/** `reopen.reason` taxonomy (A14). The UI shows the label, the API takes the code. */
export const REOPEN_REASONS = ["new_finding", "evidence_inadequate", "closed_in_error", "patient_request", "other"] as const;
export type ReopenReason = (typeof REOPEN_REASONS)[number];

/**
 * POST /cases/{id}/confirmatory-result (E8; primary_care_owner / specialist_owner who OWN the case; no body) -> 201
 * `{case_id, evidence_ref, procedure_ref, report_ref, written, recorded_at}`. The API creates a synthetic colonoscopy
 * `Procedure` and `DiagnosticReport` in HAPI and returns their references; `evidence_ref` is the Procedure, which is what
 * `complete_confirmatory.result_ref` and `close.evidence_ref` take. 409 before the colonoscopy is booked, 403 for a
 * clinician who does not own the case, 503 when HAPI is down. It appends no event.
 */
export interface ConfirmatoryResult {
  case_id: string;
  evidence_ref: string;
  procedure_ref: string;
  report_ref: string;
  recorded_at: string;
}

// ---------------------------------------------------------------------------------------
// Agency layer  (A22, PENDING)
// ---------------------------------------------------------------------------------------

/**
 * A22: the "agency". A Supervisor agent dispatches work to five department heads; each head turns the dispatch
 * into tasks for specialised executioner agents (one agent type per task). Agents are LABOUR, NOT AUTHORITY: they
 * never change case state. Anything that needs a decision becomes a `needs_human` task that shows up in the inbox
 * with prepared material, and a person then acts through the EXISTING case page controls (approve a draft,
 * reassign, close...). The agency page therefore has no button that acts on a case, only links to the case page.
 * All times are ISO-8601 strings in SIMULATED time. Actors are `system:agency:<department>` (and
 * `system:agency:supervisor`).
 *
 * Endpoints (PENDING A22; all need the bearer token):
 *   GET  /agency/org                       -> AgencyOrg                       (coordinator, supervisor)
 *   GET  /agency/runs?limit=N              -> AgencyRun[], newest first       (coordinator, supervisor)
 *   GET  /agency/tasks?status=&department=&case_id=&run_id=&limit=
 *                                          -> AgencyTask[], oldest first      (coordinator, supervisor)
 *   GET  /agency/inbox                     -> AgencyInboxItem[]; owner roles see only items on cases they own,
 *                                             coordinator and supervisor see all, others 403
 *   POST /agency/run   body {}             -> AgencyRun with `created`        (supervisor only in the UI)
 *                                             `created: false` = a run already exists for this sim time
 *   GET  /cases/{case_id}/agency           -> CaseAgencySummary               (owner roles on their own cases,
 *                                             coordinator, supervisor)
 * The http adapter degrades on a 404 (backend without A22): `agencyRuns`/`agencyTasks`/`agencyInbox` answer [],
 * `caseAgency` answers null, `agencyOrg`/`runAgency` reject with the ApiError so the page can say "not available".
 */
export type AgencyDepartment = "outreach" | "referral_scheduling" | "evidence_closure" | "patient_support" | "quality_ops";
export const AGENCY_DEPARTMENTS: readonly AgencyDepartment[] = ["outreach", "referral_scheduling", "evidence_closure", "patient_support", "quality_ops"];
export const AGENCY_DEPARTMENT_LABEL: Record<AgencyDepartment, string> = {
  outreach: "Outreach",
  referral_scheduling: "Referral & Scheduling",
  evidence_closure: "Evidence & Closure",
  patient_support: "Patient Support",
  quality_ops: "Quality & Operations",
};
export type AgencyTaskStatus = "queued" | "running" | "done" | "failed" | "needs_human";
export const AGENCY_TASK_STATUSES: readonly AgencyTaskStatus[] = ["queued", "running", "done", "failed", "needs_human"];

/** Where a model-written output came from. All null for a task that used no model. */
export interface AgencyProvenance {
  prompt_hash: string | null;
  provider: string | null;
  model_id: string | null;
  fallback_used: boolean | null;
}

export interface AgencyTask {
  task_id: string;
  run_id: string;
  parent_id: string | null;
  /** `supervisor` for the root task, else a department. A department head's task is the dispatch row (agent_type `department_head`). */
  department: AgencyDepartment | "supervisor";
  agent_type: string;
  case_id: string | null;
  status: AgencyTaskStatus;
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  provenance: AgencyProvenance;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  created_by: string;
  resolved_at: string | null;
  resolved_by: string | null;
  resolution: Record<string, unknown> | null;
}

/** Every key present, 0 if none. */
export type StatusCounts = Record<AgencyTaskStatus, number>;

export interface AgencyRun {
  run_id: string;
  trigger: "manual" | "scheduler";
  sim_at: string;
  created_by: string;
  status: "running" | "done" | "budget_exhausted" | "failed";
  planner: AgencyProvenance;
  started_at: string | null;
  finished_at: string | null;
  counts: {
    total: number;
    by_status: StatusCounts;
    by_department: Record<AgencyDepartment, StatusCounts & { total: number }>;
  };
  /** Why the supervisor dispatched (or did not dispatch) to each department. */
  rationale: Partial<Record<AgencyDepartment, string>>;
  /** Only on the answer to POST /agency/run: false when a run for this sim time already existed. */
  created?: boolean;
}

export interface AgencyExecutioner {
  agent_type: string;
  label: string;
  /** The tool allow-list: the only API calls this agent may make. */
  tools: string[];
  output_schema: string;
  /** The human actions this agent prepares or replaces (it never performs the deciding step). */
  replaces: string[];
  counts: StatusCounts;
}

export interface AgencyDepartmentNode {
  department: AgencyDepartment;
  label: string;
  actor: string;
  head_tools: string[];
  capabilities: string[];
  executioners: AgencyExecutioner[];
  counts: StatusCounts;
}

export interface AgencyOrg {
  supervisor: { actor: string; agent_type: "supervisor"; tools: string[] };
  departments: AgencyDepartmentNode[];
  last_run: AgencyRun | null;
  /** One paragraph: agents prepare work, people decide. */
  principle: string;
}

/** A `needs_human` task, with the material a person needs to decide. Nothing here acts on the case. */
export interface AgencyInboxItem {
  task_id: string;
  run_id: string;
  department: AgencyDepartment;
  agent_type: string;
  case_id: string | null;
  created_at: string;
  assignee_role: string;
  title: string;
  summary: string;
  recommended_action: string;
  material: Record<string, unknown>;
  links: { draft_id?: string };
}

export interface CaseAgencySummary {
  case_id: string;
  total: number;
  open: number;
  needs_human: number;
  tasks: { task_id: string; department: AgencyDepartment; agent_type: string; status: AgencyTaskStatus; created_at: string; title: string; summary: string }[];
}

export interface AgencyTaskParams {
  status?: AgencyTaskStatus;
  department?: AgencyDepartment;
  case_id?: string;
  run_id?: string;
  limit?: number;
}

// ---------------------------------------------------------------------------------------
// The client interface both adapters implement
// ---------------------------------------------------------------------------------------

export interface ApiClient {
  /** "mock" | "http" - for display only. */
  readonly adapter: "mock" | "http";
  /** GET /health (public): `sim_time` is the simulation clock, readable by every role (`/metrics/closure` is staff-only). */
  health(): Promise<{ status: string; sim_time?: string }>;
  /**
   * Switch identity. http: POST /auth/dev-login {role (backend role), name, provider?}, keep the bearer
   * token and attach it to every later request (requests wait for a login in flight). `provider` is sent
   * for the owner roles. mock: remembers the identity (it decides who may approve a draft), needs no token.
   */
  login(role: Role, name: string, provider?: string): Promise<void>;
  /**
   * Log in as the patient who owns `caseId` (E8). The real API needs `patient_ref` for a patient login and a patient
   * cannot look it up, so the http adapter reads it from the case with a staff login of its own (which does not
   * touch this session), remembers it, then logs in as that patient. No-op for the mock.
   */
  loginPatientFor?(caseId: string, name?: string): Promise<void>;
  /** The actor string (`role:user_id`) a demo login would have, e.g. for `reassign.owner`. http: logs in to learn the opaque id. */
  actorFor(role: Role, name: string, provider?: string): Promise<string>;
  /** Record the colonoscopy as FHIR evidence (E8, see ConfirmatoryResult). Owners of the case only. */
  recordConfirmatoryResult(caseId: string): Promise<ConfirmatoryResult>;
  /** POST /clock/advance (supervisor only): move the simulation clock forward. http only. Returns the new sim time. */
  advanceClock?(by: { days?: number; hours?: number }): Promise<string>;
  me(): Promise<Me>;
  listCases(params?: CaseListParams): Promise<CaseSummary[]>;
  getCase(caseId: string): Promise<CaseDetail>;
  getTimeline(caseId: string): Promise<CaseTimeline>;
  /** Returns the updated case. Rejects with ApiError(status 409) on an invalid transition. */
  postEvent(caseId: string, body: PostEventBody): Promise<CaseDetail>;
  requestDraft(caseId: string, body: DraftRequestBody): Promise<Draft>;
  listDrafts(caseId: string): Promise<DraftRecord[]>;
  approveDraft(draftId: string): Promise<DraftApproval>;
  rejectDraft(draftId: string, body: DraftRejectBody): Promise<DraftRejection>;
  closureMetrics(): Promise<ClosureMetrics>;
  /** GET /alerts: active and cleared platform alerts (scheduler down). */
  listAlerts(): Promise<Alert[]>;
  /** GET /agency/org (A22): the org chart with live counts. Coordinator and supervisor; others 403. */
  agencyOrg(): Promise<AgencyOrg>;
  /** GET /agency/runs?limit=N (A22): newest first. */
  agencyRuns(limit?: number): Promise<AgencyRun[]>;
  /** GET /agency/tasks (A22): oldest first. */
  agencyTasks(params?: AgencyTaskParams): Promise<AgencyTask[]>;
  /** GET /agency/inbox (A22): tasks that need a person. Owner roles see only their own cases. */
  agencyInbox(): Promise<AgencyInboxItem[]>;
  /** POST /agency/run (A22): one run per sim time; `created: false` when it already ran. */
  runAgency(): Promise<AgencyRun>;
  /** GET /cases/{id}/agency (A22): null when the backend has no agency. */
  caseAgency(caseId: string): Promise<CaseAgencySummary | null>;
  /** Mock only: restore the seeded fixtures. No-op for http. */
  resetDemo?(): Promise<void>;
  /** Mock only: raise or clear the scheduler-down alert so the dashboard banner can be demonstrated. */
  setSchedulerDown?(down: boolean): Promise<void>;
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
