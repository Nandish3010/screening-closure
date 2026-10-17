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
 *   PENDING being added by the backend agent; the UI codes against it and the mock
 *           implements it. The http adapter degrades gracefully where noted.
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

/** Side states from the pathway YAML. The engine does not emit these yet (TODO backend). */
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
 * Events the UI posts that do NOT change the state (annotations on the log).
 *
 * BACKEND: `POST /cases/{id}/events` must accept these on any non-closed case and
 * append them without a state transition (today the engine answers 409 for any event
 * that is not a transition from the current state).
 *
 *  hand_off            payload {to_provider: string}                 actor: a human
 *  hand_off_accepted   payload {provider: string}                    actor: "provider:<name>"
 *  hand_off_rejected   payload {provider: string, reason: string}    actor: "provider:<name>"
 *  escalate            payload {notify: string, note?: string}       actor: a human
 *  callback_requested  payload {reason: "patient_does_not_understand", lang: "en"|"ar"}
 *                                                                    actor: "patient:<case_id>"
 *  consent_updated     payload {channel: ContactChannel, share_with_specialist: boolean}
 *                                                                    actor: "patient:<case_id>"
 *  draft_generated     payload {draft_id, provider, fallback_used, text_ar?, text_en?}
 *                                                                    actor: "model:<provider>"
 *                      (written by the backend when POST /cases/{id}/drafts succeeds; include the
 *                      texts so the UI can show a still-pending draft after a reload. Without
 *                      them the UI says "draft text unavailable, request a new draft".)
 *  draft_approved      payload {draft_id, approved_by}               actor: a human
 *                      (written by the backend when POST /drafts/{id}/approve succeeds)
 *
 * Payload conventions on transition events (the UI sends these):
 *  accept_ownership      {owner: string, owner_role?: string}
 *  contact_patient       {channel?: ContactChannel, draft_id?: string}
 *  patient_acknowledge   {lang: "en"|"ar"}
 *  book_confirmatory     {appointment_ref: string}
 *  complete_confirmatory {result_ref: string}                         (confirmatory result reference)
 *  clinician_review      {signed_by: string}
 *  close                 {evidence_ref: string, signed_by: string}   (REQUIRED by the engine)
 */
export type EventType = TransitionEvent | NonTransitionEvent;
export type NonTransitionEvent =
  | "case_created"
  | "hand_off"
  | "hand_off_accepted"
  | "hand_off_rejected"
  | "escalate"
  | "callback_requested"
  | "consent_updated"
  | "draft_generated"
  | "draft_approved";

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
 * GET /cases?state=&owner=&overdue=true   (state is LIVE; owner/overdue and the extra fields PENDING)
 *
 * BACKEND: `owner` filters on exact owner name; `overdue=true` keeps only cases whose
 * current SLA clock has passed (`overdue_days > 0`). The UI re-applies the filters and
 * the sort on the client, so a backend that ignores them still works.
 */
export interface CaseSummary {
  case_id: string;
  pathway: string;
  state: CaseState | string;
  created_at: string;
  updated_at: string;
  /** PENDING. Name of the person who accepted ownership, or null when unowned. */
  owner?: string | null;
  /** PENDING. When the SLA for the current state expires (sim time). null when closed. */
  sla_due_at?: string | null;
  /** PENDING. Whole days past `sla_due_at` as of sim time; 0 when not overdue. */
  overdue_days?: number;
  /** PENDING. 0..1 loss-to-follow-up risk. ONLY used to order the worklist; never acts. */
  risk_score?: number;
}

export interface CaseListParams {
  state?: string;
  owner?: string;
  overdue?: boolean;
}

/** GET /cases/{id}  (LIVE: case_id, state, events. PENDING: the summary extras, optional). */
export interface CaseDetail {
  case_id: string;
  state: CaseState | string;
  events: CaseEvent[];
  owner?: string | null;
  sla_due_at?: string | null;
  overdue_days?: number;
  risk_score?: number;
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
 * BACKEND: items are sorted by `at` ascending. `kind: "event"` items are the raw log
 * rows; `sla_breach` and `escalation` items are derived by the scheduler / SLA sweep
 * and interleaved by time. The UI also derives SLA clocks, handoffs, consent state and
 * AI-draft provenance from the `event` items alone, so a timeline with only events
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
  /** Who was notified, from escalation_ladder.notify, e.g. "owner_supervisor". */
  notify: string;
  /** From escalation_ladder.action: "reminder" | "escalate". */
  action: string;
  after_breach_days: number;
  /** True once a human accepted it (every rung requires a human to accept). */
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
 * -> 201 Draft. BACKEND: also appends a `draft_generated` event (see vocabulary above).
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
 * -> 200 {draft_id, approved_by, approved_at}. BACKEND: also appends `draft_approved`
 * to the owning case. A draft is never "sent" until this succeeds.
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

/** GET /metrics/closure */
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

/** GET /me -> {role, name}. The header's role switcher is a demo override on top of this. */
export interface Me {
  role: Role;
  name: string;
}

// ---------------------------------------------------------------------------------------
// The client interface both adapters implement
// ---------------------------------------------------------------------------------------

export interface ApiClient {
  /** "mock" | "http" - for display only. */
  readonly adapter: "mock" | "http";
  health(): Promise<{ status: string }>;
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
