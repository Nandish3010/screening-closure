/**
 * Seeded fixtures for the mock adapter. ALL DATA IS SYNTHETIC (invented names, no real people).
 * Sim "now" is fixed so every demo and test is deterministic.
 */
import type { Alert, CaseEvent, ClosureMetrics, DraftRecord } from "./contract";
import { replayState, slaClock } from "../lib/derive";

export const SIM_NOW = "2026-11-04T09:00:00.000Z";
const DAY = 86_400_000;
const at = (daysAgo: number) => new Date(new Date(SIM_NOW).getTime() - daysAgo * DAY).toISOString();

export const DEMO_PATIENT_CASE_ID = "c0000007-0000-4000-8000-000000000007";
/** Most overdue case: sits at confirmatory_completed, ready for escalate -> draft -> sign-off. */
export const DEMO_OVERDUE_CASE_ID = "c0000001-0000-4000-8000-000000000001";

type Step = [type: string, daysAgo: number, actor: string, payload?: Record<string, unknown>];

function build(id: string, createdDaysAgo: number, steps: Step[]): CaseEvent[] {
  const evs: CaseEvent[] = [
    { seq: 1, type: "case_created", at: at(createdDaysAgo), actor: "system:intake", payload: { patient_ref: `synthetic-patient-${id.slice(-3)}` } },
  ];
  steps.forEach(([type, d, actor, payload], i) =>
    evs.push({ seq: i + 2, type, at: at(d), actor, payload: payload ?? {} }),
  );
  return evs;
}

/**
 * Owners. The owner IS the actor of the accepting event (A5): `primary_care_owner:<name>`. The role label
 * the patient sees travels in `owner_role`. Layla, Huda (clinician) and Karim (specialist) are the demo
 * clinician logins in AppContext; Sara and Omar are other owners the demo cases belong to.
 */
const HASH_1 = "9f2c4e7a1b3d5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8";
const HASH_2 = "4b1e8d3c7a95f2e60d1c8b7a6f5e4d3c2b1a09f8e7d6c5b4a39281706f5e4d3c";
const LAYLA_ACTOR = "primary_care_owner:Dr. Layla Haddad";
const SARA_ACTOR = "primary_care_owner:Sara Al Mansoori";
const OMAR_ACTOR = "primary_care_owner:Omar Khalid";
const HUDA_ACTOR = "primary_care_owner:Dr. Huda Rahman";
const KARIM_ACTOR = "specialist_owner:Dr. Karim Nasser";
const LAYLA = { owner_role: "Primary care physician" };
const SARA = { owner_role: "Care coordinator" };
const OMAR = { owner_role: "Care coordinator" };
const HUDA = { owner_role: "Primary care physician" };
const COORD = "coordinator:Maryam Al Suwaidi";
const sentPayload = (draft_id: string, channel = "sms", language = "en", purpose = "result_notification") =>
  ({ draft_id, notification_id: `n-${draft_id}`, channel, language, purpose });

export const FIXTURE_RISK: Record<string, number> = {};
const defs: { id: string; risk: number; events: CaseEvent[] }[] = [];
/** Cases that exist only in a demo with automation on (A21): they show what the automation actor already did. */
const autoDefs: { id: string; risk: number; events: CaseEvent[] }[] = [];
export const caseIdFor = (n: number) => `c${String(n).padStart(7, "0")}-0000-4000-8000-${String(n).padStart(12, "0")}`;
function add(n: number, risk: number, createdDaysAgo: number, steps: Step[], into = defs) {
  const id = caseIdFor(n);
  FIXTURE_RISK[id] = risk;
  into.push({ id, risk, events: build(id, createdDaysAgo, steps) });
}

// 1. Most overdue: confirmatory completed 30 days ago, clinician review (5 working days) not done.
add(1, 0.62, 95, [
  ["acknowledge", 94, "lab:Central Lab"],
  ["accept_ownership", 92, LAYLA_ACTOR, LAYLA],
  ["message_drafted", 91, "model:template", { draft_id: "d-seed-1", provider: "template", fallback_used: false, prompt_hash: HASH_1, language: "en" }],
  ["message_approved", 91, LAYLA_ACTOR, { draft_id: "d-seed-1" }],
  ["message_sent", 91, "system:notifier", sentPayload("d-seed-1")],
  ["contact_patient", 90, COORD, { channel: "phone", draft_id: "d-seed-1" }],
  ["handoff_requested", 89, LAYLA_ACTOR, { to_provider: "Al Noor Endoscopy Centre" }],
  ["handoff_rejected", 88, "specialist_owner:Dr. Rana Farouk", { reason: "capacity" }],
  ["patient_acknowledge", 86, "patient:c0000001", { lang: "ar" }],
  ["consent_updated", 86, "patient:c0000001", { channel_whatsapp: true, share_with_specialist: true }],
  ["book_confirmatory", 70, KARIM_ACTOR, { appointment_ref: "APPT-2026-0412" }],
  ["complete_confirmatory", 30, KARIM_ACTOR, { result_ref: "DiagnosticReport/colonoscopy-0001" }],
]);
// 2. Patient contacted 22 days ago; acknowledgement (5 working days) overdue.
add(2, 0.81, 40, [
  ["acknowledge", 39, "lab:Central Lab"],
  ["accept_ownership", 37, SARA_ACTOR, SARA],
  ["message_drafted", 23, "model:template", { draft_id: "d-seed-2", provider: "template", fallback_used: true, prompt_hash: HASH_2, language: "en" }],
  ["contact_patient", 22, COORD, { channel: "sms" }],
  ["escalate", 5, "system:scheduler", { rung: "supervisor", target_role: "supervisor" }],
]);
// 3. Owner accepted 16 days ago; patient contact (5 working days) overdue.
add(3, 0.7, 25, [
  ["acknowledge", 24, "lab:Central Lab"],
  ["accept_ownership", 16, OMAR_ACTOR, OMAR],
]);
// 4. Result received 6 days ago, nobody acknowledged (2 working days) -> overdue, unowned.
add(4, 0.55, 6, []);
// 5. Acknowledged yesterday, unowned, not overdue.
add(5, 0.4, 3, [["acknowledge", 1, "lab:Central Lab"]]);
// 6. Owner accepted 2 days ago, not overdue.
add(6, 0.35, 5, [["acknowledge", 4, "lab:Central Lab"], ["accept_ownership", 2, LAYLA_ACTOR, LAYLA]]);
// 7. Patient demo case: patient contacted 2 days ago, waiting for the patient to acknowledge.
add(7, 0.3, 9, [
  ["acknowledge", 8, "lab:Central Lab"],
  ["accept_ownership", 6, SARA_ACTOR, SARA],
  ["contact_patient", 2, COORD, { channel: "sms" }],
]);
// 8. Patient acknowledged 3 days ago.
add(8, 0.25, 20, [
  ["acknowledge", 19, "lab:Central Lab"],
  ["accept_ownership", 17, OMAR_ACTOR, OMAR],
  ["contact_patient", 10, COORD, { channel: "phone" }],
  ["patient_acknowledge", 3, "patient:c0000008", { lang: "en" }],
]);
// 9. Confirmatory booked, after a referral that Karim (Corniche Gastro Unit) accepted: he owns the case now,
//    and the 31-working-day confirmatory clock runs from his acceptance (A13), not from the booking.
add(9, 0.2, 30, [
  ["acknowledge", 29, "lab:Central Lab"],
  ["accept_ownership", 27, LAYLA_ACTOR, LAYLA],
  ["contact_patient", 24, COORD, { channel: "phone" }],
  ["patient_acknowledge", 22, "patient:c0000009", { lang: "ar" }],
  ["handoff_requested", 21, LAYLA_ACTOR, { to_provider: "Corniche Gastro Unit" }],
  ["handoff_accepted", 20, KARIM_ACTOR, {}],
  ["book_confirmatory", 12, KARIM_ACTOR, { appointment_ref: "APPT-2026-0577" }],
]);
// 10. Clinician reviewed 1 day ago, awaiting signed closure (3 working days). Owned by Dr. Huda Rahman, so the
//     clinician login can sign it off.
add(10, 0.12, 60, [
  ["acknowledge", 59, "lab:Central Lab"],
  ["accept_ownership", 58, HUDA_ACTOR, HUDA],
  ["contact_patient", 55, COORD, { channel: "phone" }],
  ["patient_acknowledge", 52, "patient:c0000010", { lang: "en" }],
  // The referral milestone (A18) is met by the request. Dr. Huda's own unit accepts, so she still owns the case and the
  // clinician login can sign it off (a seeded shortcut: the mock only validates events posted after the seed).
  ["handoff_requested", 48, HUDA_ACTOR, { to_provider: "Corniche Gastro Unit" }],
  ["handoff_accepted", 47, HUDA_ACTOR, {}],
  ["book_confirmatory", 45, COORD, { appointment_ref: "APPT-2026-0310" }],
  ["complete_confirmatory", 8, KARIM_ACTOR, { result_ref: "DiagnosticReport/colonoscopy-0010" }],
  ["clinician_review", 1, HUDA_ACTOR, { signed_by: "Dr. Huda Rahman" }],
]);
// 11. Closed with evidence and a human sign-off (Dr. Huda Rahman, who can reopen it).
add(11, 0.05, 80, [
  ["acknowledge", 79, "lab:Central Lab"],
  ["accept_ownership", 78, HUDA_ACTOR, HUDA],
  ["contact_patient", 75, COORD, { channel: "sms" }],
  ["patient_acknowledge", 72, "patient:c0000011", { lang: "en" }],
  ["handoff_requested", 69, HUDA_ACTOR, { to_provider: "Corniche Gastro Unit" }],
  ["handoff_accepted", 68, HUDA_ACTOR, {}],
  ["book_confirmatory", 65, COORD, { appointment_ref: "APPT-2026-0201" }],
  ["complete_confirmatory", 40, KARIM_ACTOR, { result_ref: "DiagnosticReport/colonoscopy-0011" }],
  ["clinician_review", 36, HUDA_ACTOR, { signed_by: "Dr. Huda Rahman" }],
  ["close", 35, HUDA_ACTOR, { evidence_ref: "DiagnosticReport/colonoscopy-0011", signed_by: "Dr. Huda Rahman" }],
]);
// 12. Overdue not at all, but high risk: ordering tie-breaker demo (acknowledged, owner accepted today).
add(12, 0.9, 4, [["acknowledge", 3, "lab:Central Lab"], ["accept_ownership", 0, SARA_ACTOR, SARA]]);
// 13. Owner accepted 12 days ago and nobody has touched it since: overdue, and the watchdog flagged it.
add(13, 0.45, 14, [
  ["acknowledge", 13, "lab:Central Lab"],
  ["accept_ownership", 12, OMAR_ACTOR, OMAR],
  ["watchdog_flagged", 1, "watchdog", { reason: "idle", idle_days: 11 }],
]);
// 14. Patient contacted, then withdrew consent: outreach is frozen and the case is on the human-review list.
add(14, 0.5, 9, [
  ["acknowledge", 8, "lab:Central Lab"],
  ["accept_ownership", 7, OMAR_ACTOR, OMAR],
  ["contact_patient", 4, COORD, { channel: "sms" }],
  ["consent_updated", 2, "patient:c0000014", { withdrawn: true }],
  ["review_requested", 2, "system:consent", { reason: "consent_withdrawn", source_event_id: "seed-consent-14" }],
]);
// 15. A referral nobody answered: Dr. Layla asked Marina Digestive Health 9 days ago, the scheduler bounced it after 3
//     working days (no response), and the case sits in referral_rejected waiting for a re-route (2 working days).
add(15, 0.58, 32, [
  ["acknowledge", 31, "lab:Central Lab"],
  ["accept_ownership", 29, LAYLA_ACTOR, LAYLA],
  ["contact_patient", 26, COORD, { channel: "phone" }],
  ["patient_acknowledge", 24, "patient:c0000015", { lang: "en" }],
  ["handoff_requested", 9, LAYLA_ACTOR, { to_provider: "Marina Digestive Health" }],
  ["handoff_rejected", 5, "system:scheduler", { reason: "timeout", bounced: true }],
]);
// 16. A handoff waiting for acceptance: Dr. Layla asked Corniche Gastro Unit yesterday. Dr. Karim Nasser (that provider)
//     can answer it; nobody else can take the case over.
add(16, 0.33, 8, [
  ["acknowledge", 7, "lab:Central Lab"],
  ["accept_ownership", 6, LAYLA_ACTOR, LAYLA],
  ["handoff_requested", 1, LAYLA_ACTOR, { to_provider: "Corniche Gastro Unit" }],
]);

/** The actor of every automatic step (A21). */
const AUTO = "system:automation";
const TEMPLATE_SMS = { channel: "sms", language: "en", purpose: "result_notification", auto: true, template_id: "result_notification.sms", template_version: 1 };

/** Case 17 (automation demo): the evidence scan found the colonoscopy and proposed closure; Dr. Huda Rahman, the owner, signs off. */
export const DEMO_CLOSURE_PROPOSED_CASE_ID = caseIdFor(17);
/** Case 18 (automation demo): the re-route list ran out; a coordinator must reassign. */
export const DEMO_REROUTE_EXHAUSTED_CASE_ID = caseIdFor(18);
/** Case 19 (automation demo): filed and assigned automatically this morning; Dr. Layla Haddad has not accepted it yet. */
export const DEMO_ASSIGNED_CASE_ID = caseIdFor(19);

add(17, 0.28, 14, [
  ["acknowledge", 13.9, AUTO, { auto: true }],
  ["assign_ownership", 13.9, AUTO, { owner: HUDA_ACTOR, provider: "Marina Digestive Health", auto: true }],
  ["accept_ownership", 13, HUDA_ACTOR, HUDA],
  ["message_sent", 13, AUTO, { ...TEMPLATE_SMS, notification_id: "n-auto-17-1" }],
  ["contact_patient", 11, COORD, { channel: "phone" }],
  ["patient_acknowledge", 10, "patient:c0000017", { lang: "en" }],
  ["handoff_requested", 8, HUDA_ACTOR, { to_provider: "Corniche Gastro Unit" }],
  ["handoff_accepted", 7, HUDA_ACTOR, {}],
  ["book_confirmatory", 6, COORD, { appointment_ref: "APPT-2026-0618" }],
  ["complete_confirmatory", 0.5, AUTO, { result_ref: "Procedure/colonoscopy-0017", auto: true }],
  ["closure_proposed", 0.5, AUTO, { evidence_ref: "Procedure/colonoscopy-0017", auto: true }],
], autoDefs);
add(18, 0.52, 20, [
  ["acknowledge", 19.9, AUTO, { auto: true }],
  ["assign_ownership", 19.9, AUTO, { owner: LAYLA_ACTOR, provider: "Riverside Primary Care", auto: true }],
  ["accept_ownership", 19, LAYLA_ACTOR, LAYLA],
  ["message_sent", 19, AUTO, { ...TEMPLATE_SMS, notification_id: "n-auto-18-1" }],
  ["contact_patient", 17, COORD, { channel: "phone" }],
  ["patient_acknowledge", 16, "patient:c0000018", { lang: "ar" }],
  ["handoff_requested", 3, LAYLA_ACTOR, { to_provider: "Al Noor Endoscopy Centre" }],
  ["handoff_rejected", 1.4, "specialist_owner:Dr. Rana Farouk", { reason: "capacity" }],
  ["handoff_requested", 1.4, AUTO, { to_provider: "Corniche Gastro Unit", auto: true }],
  ["handoff_rejected", 1, KARIM_ACTOR, { reason: "wrong_service" }],
  ["handoff_requested", 1, AUTO, { to_provider: "Marina Digestive Health", auto: true }],
  ["handoff_rejected", 0.6, HUDA_ACTOR, { reason: "capacity" }],
  ["review_requested", 0.6, AUTO, { reason: "reroute_exhausted", source_event_id: "seed-reroute-18" }],
], autoDefs);
add(19, 0.38, 1, [
  ["acknowledge", 0.9, AUTO, { auto: true }],
  ["assign_ownership", 0.9, AUTO, { owner: LAYLA_ACTOR, provider: "Riverside Primary Care", auto: true }],
], autoDefs);

/** The reply triage (D13): it reads a patient's reply and writes only `reply_received`, `reply_classified` and a review flag. */
const TRIAGE = "system:triage";
/** Case 20 (automation demo): the patient replied "stop" by SMS; the triage flagged an opt-out and the automation sends nothing more. */
export const DEMO_OPT_OUT_CASE_ID = caseIdFor(20);
/** Case 21 (automation demo): the patient replied that this is not their number; the triage flagged a wrong number (an outreach hold). */
export const DEMO_WRONG_NUMBER_CASE_ID = caseIdFor(21);

add(20, 0.41, 6, [
  ["acknowledge", 5.9, AUTO, { auto: true }],
  ["assign_ownership", 5.9, AUTO, { owner: LAYLA_ACTOR, provider: "Riverside Primary Care", auto: true }],
  ["accept_ownership", 5, LAYLA_ACTOR, LAYLA],
  ["message_sent", 5, AUTO, { ...TEMPLATE_SMS, notification_id: "n-auto-20-1" }],
  ["reply_received", 2, TRIAGE, { channel: "sms", text_ref: "7d1b2f0e-5c4a-4e8b-9a3d-0f6e1c2b3a41" }],
  ["reply_classified", 2, TRIAGE, { intent: "opt_out", confidence: 0.97, model_id: "triage-rules-1", needs_human: true, text_ref: "7d1b2f0e-5c4a-4e8b-9a3d-0f6e1c2b3a41" }],
  ["review_requested", 2, TRIAGE, { reason: "opt_out", source_event_id: "seed-reply-20" }],
], autoDefs);
add(21, 0.36, 5, [
  ["acknowledge", 4.9, AUTO, { auto: true }],
  ["assign_ownership", 4.9, AUTO, { owner: HUDA_ACTOR, provider: "Riverside Primary Care", auto: true }],
  ["accept_ownership", 4, HUDA_ACTOR, HUDA],
  ["message_sent", 4, AUTO, { ...TEMPLATE_SMS, notification_id: "n-auto-21-1" }],
  ["reply_received", 1, TRIAGE, { channel: "sms", text_ref: "3a9c8e51-26d7-4b0f-8c14-9e2d7a5f6b10" }],
  ["reply_classified", 1, TRIAGE, { intent: "wrong_number", confidence: 0.91, model_id: "triage-rules-1", needs_human: true, text_ref: "3a9c8e51-26d7-4b0f-8c14-9e2d7a5f6b10" }],
  ["review_requested", 1, TRIAGE, { reason: "wrong_number", source_event_id: "seed-reply-21" }],
], autoDefs);

/** The scheduler's `mark_overdue` for every seeded case whose SLA clock has already passed. */
function withOverdueMarkers(events: CaseEvent[]): CaseEvent[] {
  const clock = slaClock(events, replayState(events), SIM_NOW);
  if (!clock.due_at || new Date(SIM_NOW) <= new Date(clock.due_at)) return events;
  const marked: CaseEvent[] = [
    ...events,
    { seq: 0, type: "mark_overdue", at: clock.due_at, actor: "system:scheduler", payload: { state: replayState(events), due_at: clock.due_at } },
  ];
  marked.sort((a, b) => a.at.localeCompare(b.at)); // stable: same-instant events keep their order
  return marked.map((e, i) => ({ ...e, seq: i + 1 }));
}

/** The seeded cases; with `automation` the demo also gets the cases the automation actor has already worked on (A21). */
export function seedEvents(automation = false): Record<string, CaseEvent[]> {
  const all = automation ? [...defs, ...autoDefs] : defs;
  return structuredClone(Object.fromEntries(all.map((d) => [d.id, withOverdueMarkers(d.events)])));
}

export const SEED_METRICS: Omit<ClosureMetrics, "sim_time"> = {
  p50_days: 38,
  p90_days: 79,
  open_by_age_bucket: { "0-30": 41, "31-60": 23, "61-90": 11, "90+": 6 },
  breaches_by_provider: [
    { provider: "Al Noor Endoscopy Centre", count: 14 },
    { provider: "Corniche Gastro Unit", count: 9 },
    { provider: "Marina Digestive Health", count: 6 },
    { provider: "Central Lab", count: 3 },
  ],
  bottleneck_by_transition: [
    { transition: "complete_confirmatory", median_days: 31 },
    { transition: "book_confirmatory", median_days: 12 },
    { transition: "patient_acknowledge", median_days: 6 },
    { transition: "contact_patient", median_days: 4 },
    { transition: "clinician_review", median_days: 3 },
    { transition: "accept_ownership", median_days: 2 },
    { transition: "acknowledge", median_days: 1 },
  ],
};

/**
 * The drafts the seeded cases already have (GET /cases/{id}/drafts). Text lives here, not on the timeline events.
 * E14: the texts follow the API templates and state no finding (a test in web/tests/mockAdapter.test.ts checks this).
 */
export function seedDrafts(): Record<string, DraftRecord> {
  const evs = seedEvents();
  const firstDraft = (caseNo: number, draftId: string) =>
    evs[`c${String(caseNo).padStart(7, "0")}-0000-4000-8000-${String(caseNo).padStart(12, "0")}`].find(
      (e) => e.type === "message_drafted" && e.payload.draft_id === draftId,
    )!;
  const d1 = firstDraft(1, "d-seed-1");
  const d2 = firstDraft(2, "d-seed-2");
  const approved = evs["c0000001-0000-4000-8000-000000000001"].find((e) => e.type === "message_approved")!;
  return {
    "d-seed-1": {
      draft_id: "d-seed-1", case_id: DEMO_OVERDUE_CASE_ID, purpose: "result_notification", channel: "sms", language: "en",
      text: "Your screening result is ready and your care team needs to talk with you about the next step. Your care team will call you, or you can reply to book a time. The next step is a camera test.",
      text_ar: null, text_en: "Your screening result is ready and your care team needs to talk with you about the next step. Your care team will call you, or you can reply to book a time. The next step is a camera test.",
      provider: "template", fallback_used: false, abstention_reason: null, prompt_hash: HASH_1, model_id: null,
      created_by: "coordinator:Maryam Al Suwaidi", created_at: d1.at,
      approved_at: approved.at, approved_by: approved.actor, rejected_at: null, rejected_by: null, rejection_reason: null,
    },
    "d-seed-2": {
      draft_id: "d-seed-2", case_id: "c0000002-0000-4000-8000-000000000002", purpose: "reminder", channel: "sms", language: "en",
      text: "Reminder from your care team. Your screening result is ready and your care team still needs to talk with you about the next step. Your care team will call you, or you can reply to book a time.",
      text_ar: null, text_en: "Reminder from your care team. Your screening result is ready and your care team still needs to talk with you about the next step. Your care team will call you, or you can reply to book a time.",
      provider: "template", fallback_used: true, abstention_reason: "provider_unavailable", prompt_hash: HASH_2, model_id: null,
      created_by: "coordinator:Maryam Al Suwaidi", created_at: d2.at,
      approved_at: null, approved_by: null, rejected_at: null, rejected_by: null, rejection_reason: null,
    },
  };
}

/** No platform alerts at start; the mock can raise a scheduler-down alert for the demo (dashboard banner). */
export const SEED_ALERTS: Alert[] = [];
